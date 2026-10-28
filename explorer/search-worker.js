/* Runs from an inline Blob, including when index.html is opened with file://. */
let corpus, dimensions, count, queryCache, settings, extractorPromise, runtimeSource, contextTokens;
let queue = Promise.resolve();

function decodeFloat32(encoded) {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Float32Array(bytes.buffer);
}

function rank(vector, limit) {
  if (vector.length !== dimensions || vector.some(v => !Number.isFinite(v))) {
    throw new Error('The query encoder returned an invalid embedding.');
  }
  let norm = 0;
  for (const v of vector) norm += v * v;
  norm = Math.sqrt(norm);
  if (norm < 1e-8) throw new Error('The query encoder returned an empty embedding.');
  const ranked = [];
  for (let i = 0; i < count; i++) {
    let dot = 0;
    const offset = i * dimensions;
    for (let j = 0; j < dimensions; j++) dot += corpus[offset + j] * vector[j] / norm;
    ranked.push({index: i, similarity: Math.max(-1, Math.min(1, dot))});
  }
  return ranked.sort((a, b) => b.similarity - a.similarity || a.index - b.index).slice(0, limit);
}

async function loadEncoder(requestId) {
  if (!extractorPromise) {
    extractorPromise = (async () => {
      postMessage({type: 'progress', requestId, text: 'Loading local embedding model · first use downloads ~230 MB'});
      const libraryURL = URL.createObjectURL(new Blob([runtimeSource], {type: 'text/javascript'}));
      let library;
      try {library = await import(libraryURL);} finally {URL.revokeObjectURL(libraryURL);}
      const {AutoConfig, AutoModel, AutoTokenizer, env} = library;
      env.allowLocalModels = false;
      env.useBrowserCache = true;
      // Single-threaded WASM also works without a server's isolation headers.
      env.backends.onnx.wasm.numThreads = 1;
      env.backends.onnx.wasm.proxy = false;
      const downloads = new Map();
      let lastUpdate = 0;
      const progress_callback = event => {
        if (event.status === 'progress') {
          downloads.set(event.file, {loaded: event.loaded || 0, total: event.total || 0});
          const now = Date.now();
          if (now - lastUpdate > 150) {
            const loaded = [...downloads.values()].reduce((sum, f) => sum + f.loaded, 0);
            postMessage({type: 'progress', requestId,
              text: `Loading local embedding model · ${(loaded / 1e6).toFixed(0)} MB downloaded`});
            lastUpdate = now;
          }
        }
      };
      const options = {revision: settings.revision, progress_callback};
      const config = await AutoConfig.from_pretrained(settings.model, options);
      config.vision_config = null;
      config.audio_config = null;
      let device = 'wasm';
      if (navigator.gpu) {
        const adapter = await navigator.gpu.requestAdapter();
        if (adapter) device = 'webgpu';
      }
      const tokenizer = await AutoTokenizer.from_pretrained(settings.model, options);
      let model;
      try {
        model = await AutoModel.from_pretrained(settings.model, {...options, config, device, dtype: 'q4'});
      } catch (error) {
        if (device === 'wasm') throw error;
        postMessage({type: 'progress', requestId, text: 'WebGPU unavailable · using CPU search'});
        device = 'wasm';
        model = await AutoModel.from_pretrained(settings.model, {...options, config, device, dtype: 'q4'});
      }
      postMessage({type: 'ready', device});
      return {tokenizer, model};
    })().catch(error => {extractorPromise = null; throw error;});
  }
  return extractorPromise;
}

async function search(message) {
  const {requestId, query, limit = 50} = message;
  try {
    const key = query.trim().toLocaleLowerCase().replace(/\s+/g, ' ');
    let vector = queryCache.get(key);
    let precomputed = true;
    if (!vector) {
      precomputed = false;
      const {tokenizer, model} = await loadEncoder(requestId);
      postMessage({type: 'progress', requestId, text: 'Finding papers by meaning…'});
      const input = await tokenizer('task: search result | query: ' + query,
        {padding: true, truncation: false});
      const output = await model(input);
      if (!output.sentence_embedding) throw new Error('The browser model did not return a sentence embedding.');
      vector = Float32Array.from(output.sentence_embedding.data);
      if (queryCache.size > 140) queryCache.delete([...queryCache.keys()][12]);
      queryCache.set(key, vector);
    }
    postMessage({type: 'results', requestId, query, precomputed, results: rank(vector, limit)});
  } catch (error) {
    postMessage({type: 'error', requestId,
      text: 'Custom semantic search could not run. The demo topics, researcher and branch filters, and paper neighbors work offline. ' + error.message});
  }
}

async function embedPaper(message) {
  const {requestId, title, abstract} = message;
  try {
    const {tokenizer, model} = await loadEncoder(requestId);
    postMessage({type: 'progress', requestId, text: 'Embedding the paper’s title and abstract locally…'});
    // The identical titled-document format used by embed_publications.document().
    const text = 'title: ' + (title.trim() || 'none') + ' | text: ' + (abstract || '').trim();
    const input = await tokenizer(text, {padding: true, truncation: false});
    const tokens = input.input_ids?.dims?.at(-1);
    if (!tokens || tokens > contextTokens) throw new Error(`This paper exceeds the ${contextTokens.toLocaleString()}-token model context. Its text has not been truncated or added.`);
    const output = await model(input);
    if (!output.sentence_embedding) throw new Error('The browser model did not return a sentence embedding.');
    const vector = Float32Array.from(output.sentence_embedding.data);
    if (vector.length !== dimensions || vector.some(v => !Number.isFinite(v))) throw new Error('The model returned an invalid paper embedding.');
    const norm = Math.hypot(...vector);
    if (norm < 1e-8) throw new Error('The model returned an empty paper embedding.');
    for (let i = 0; i < vector.length; i++) vector[i] /= norm;
    postMessage({type: 'paper-vector', requestId, vector});
  } catch (error) {postMessage({type: 'error', requestId, text: 'The paper could not be embedded. ' + error.message});}
}

self.onmessage = event => {
  const message = event.data;
  if (message.type === 'init') {
    dimensions = message.dimensions;
    count = message.count;
    settings = message.settings;
    contextTokens = message.contextTokens || 8192;
    runtimeSource = message.runtimeSource;
    corpus = decodeFloat32(message.vectors);
    queryCache = new Map(message.queries.map(q => [q.text.toLocaleLowerCase(), Float32Array.from(q.vector)]));
    if (corpus.length !== count * dimensions) throw new Error('Invalid embedded corpus.');
    postMessage({type: 'initialized'});
  } else if (message.type === 'append') {
    const vector = Float32Array.from(message.vector);
    if (vector.length !== dimensions || vector.some(v => !Number.isFinite(v))) throw new Error('Invalid added paper vector.');
    const expanded = new Float32Array(corpus.length + dimensions);
    expanded.set(corpus); expanded.set(vector, corpus.length); corpus = expanded; count++;
  } else if (message.type === 'embed-paper') {
    queue = queue.then(() => embedPaper(message));
  } else if (message.type === 'search') {
    const key = message.query.trim().toLocaleLowerCase().replace(/\s+/g, ' ');
    // Offline/demo queries remain instant even while a custom model is loading.
    if (queryCache.has(key)) search(message);
    else queue = queue.then(() => search(message));
  }
};
