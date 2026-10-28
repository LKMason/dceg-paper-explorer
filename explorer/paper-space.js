(function (root) {
  'use strict';
  function decode(encoded) {
    const raw = atob(encoded), bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return new Float32Array(bytes.buffer);
  }
  function normalize(input, dimensions) {
    const vector = Float32Array.from(input);
    if (vector.length !== dimensions || vector.some(v => !Number.isFinite(v))) throw new Error('Invalid paper embedding.');
    const norm = Math.hypot(...vector);
    if (norm < 1e-8) throw new Error('Empty paper embedding.');
    return vector.map(v => v / norm);
  }
  function distance(corpus, dimensions, index, vector) {
    let dot = 0;
    for (let j = 0; j < dimensions; j++) dot += corpus[index * dimensions + j] * vector[j];
    return Math.max(0, Math.min(2, 1 - dot));
  }
  function plan(vector, corpus, dimensions, coordinates, pca, baseCount, neighborCount = 10) {
    vector = normalize(vector, dimensions);
    const count = corpus.length / dimensions;
    if (!Number.isInteger(count) || !pca || pca.mean.length !== dimensions || pca.components.length !== 3
      || pca.components.some(row => row.length !== dimensions)) throw new Error('Missing fitted PCA transform. Rebuild the explorer before adding papers.');
    const ranked = Array.from({length: count}, (_, index) => ({index, distance: distance(corpus, dimensions, index, vector)}))
      .sort((a, b) => a.distance - b.distance || a.index - b.index);
    const anchors = ranked.filter(r => r.index < baseCount).slice(0, neighborCount);
    const identical = anchors.filter(r => r.distance < 1e-7);
    const weights = (identical.length ? identical : anchors).map(r => ({...r, weight: identical.length ? 1 : 1 / (r.distance + 1e-6) ** 2}));
    const weightSum = weights.reduce((sum, r) => sum + r.weight, 0);
    const positions = {pca: pca.components.map(row => row.reduce((sum, v, j) => sum + v * (vector[j] - pca.mean[j]), 0))};
    for (const method of ['umap', 'tsne']) positions[method] = [0, 1, 2].map(axis => weights.reduce((sum, r) => sum + coordinates[method][r.index][axis] * r.weight, 0) / weightSum);
    if (Object.values(positions).some(point => point.some(v => !Number.isFinite(v)))) throw new Error('Could not place this paper in the space.');
    const expanded = new Float32Array(corpus.length + dimensions); expanded.set(corpus); expanded.set(vector, corpus.length);
    return {vector, corpus: expanded, positions, ranked, neighbors: ranked.slice(0, neighborCount).map(r => [r.index, r.distance])};
  }
  const api = {decode, normalize, distance, plan};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PaperSpace = api;
})(globalThis);
