(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const data = JSON.parse($('#explorer-data').textContent);
  const papers = data.papers;
  const baseCount = papers.length, addedRecords = [];
  let corpus = PaperSpace.decode(data.vectors);
  const storageKey = 'dceg-explorer-added-v1:' + (data.sourceFingerprint || data.createdAt) + ':' + (data.modelRevision || '');
  data.authorTypes ||= {};
  const byId = new Map(papers.map((p, i) => [p.id, i]));
  const branchNames = new Map(data.branches.map(b => [b.code, b.name]));
  const escape = text => String(text ?? '').replace(/[&<>"']/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const normalize = text => text.trim().toLocaleLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  const citation = p => `${p.firstAuthor.split(/\s+/).pop()}${p.authors.length > 1 ? ' et al.' : ''} · ${p.year || 'undated'}`;
  const safeURL = value => {try {const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) ? u.href : '';} catch {return '';}};
  let saved = {};
  try {saved = JSON.parse(localStorage.getItem('dceg-explorer-view') || '{}');} catch { /* Storage is optional. */ }
  const initial = byId.get(saved.paper) ?? byId.get('pmid:42031785') ?? 0;
  const view = {
    mode: ['search', 'neighbors', 'compare', 'add', 'buddy'].includes(saved.mode) ? saved.mode : 'search',
    projection: ['umap', 'tsne', 'pca'].includes(saved.projection) ? saved.projection : 'umap',
    color: ['year', 'branch', 'author'].includes(saved.color) ? saved.color : 'year',
    rotation: -.45, tilt: .27, zoom: 1, expanded: false,
  };
  const makeState = selected => ({selected, comparison: null, tab: 'text', expandedPapers: new Set(),
    query: '', queryB: '', draft: '', draftB: '', kind: 'semantic', results: [], matches: new Map(),
    choiceA: null, choiceB: null, querySpecA: null, querySpecB: null,
    version: 0, busy: false, progress: '', error: '', filter: 'all', resultsA: null, resultsB: null,
    counts: {all: 0, a: 0, b: 0, both: 0}, listScroll: 0, paperScroll: 0});
  const states = {search: makeState(null), neighbors: makeState(initial), compare: makeState(null),
    add: Object.assign(makeState(null), {pmid: '', notice: '', history: [], storageWarning: ''}),
    buddy: Object.assign(makeState(null), {authorDraft: '', author: '', buddy: null, rankings: [], total: 0,
      minimumPapers: 3, epsilon: .05, excludeCoauthors: false})};
  let state = states[view.mode];
  let requestCounter = 0, activeCompareField = 'search';
  const jobs = new Map();
  const authorIndex = new Map();
  const branchIndex = new Map();
  papers.forEach((p, i) => {
    for (const a of new Set(p.authors)) {if (!authorIndex.has(a)) authorIndex.set(a, []); authorIndex.get(a).push(i);}
    for (const b of p.branches) {if (!branchIndex.has(b)) branchIndex.set(b, []); branchIndex.get(b).push(i);}
  });
  const topBranches = [...branchIndex].sort((a, b) => b[1].length - a[1].length).slice(0, 6).map(([key]) => key);
  const counts = new Map();
  papers.forEach(p => counts.set(p.firstAuthor, (counts.get(p.firstAuthor) || 0) + 1));
  const topAuthors = [...counts].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([key]) => key);
  const recentYears = [...new Set(papers.map(p => p.year).filter(Boolean))].sort().reverse().slice(0, 3);
  const canvas = $('#canvas');
  const context = canvas.getContext('2d');
  let width = 0, height = 0, projected = [], drag = null, moved = false, frame = null;
  let worker = null, buddyWorker = null;
  const palette = ['--blue', '--coral', '--gold', '--green', '--purple', '--teal', '--rose', '--gray']
    .map(token => getComputedStyle(document.documentElement).getPropertyValue(token).trim());
  const ink = getComputedStyle(document.documentElement).getPropertyValue('--ink').trim();
  const coords = {}, coordinateFrames = {};
  for (const [method, points] of Object.entries(data.coordinates)) {
    const center = [0, 1, 2].map(axis => points.reduce((sum, p) => sum + p[axis], 0) / points.length);
    let max = 0;
    points.forEach(p => p.forEach((v, j) => {max = Math.max(max, Math.abs(v - center[j]));}));
    coordinateFrames[method] = {center, scale: max || 1};
    coords[method] = points.map(p => p.map((v, j) => (v - center[j]) / (max || 1)));
  }

  function persist() {
    try {localStorage.setItem('dceg-explorer-view', JSON.stringify({
      paper: papers[state.selected ?? initial].id, projection: view.projection, color: view.color, mode: view.mode,
    }));} catch { /* Private browsing can disable persistence. */ }
  }

  function error(message) {state.error = message; $('#error span').textContent = message; $('#error').hidden = false;}
  function hideSuggestions() {for (const id of ['search', 'search-b', 'buddy-author']) {const input = $('#' + id); input.setAttribute('aria-expanded', 'false'); $('#' + input.getAttribute('aria-controls')).hidden = true;}}

  function category(p) {
    if (view.color === 'year') {const i = recentYears.indexOf(p.year); return i < 0 ? 3 : i;}
    if (view.color === 'branch') {const i = topBranches.findIndex(b => p.branches.includes(b)); return i < 0 ? 7 : i;}
    const i = topAuthors.indexOf(p.firstAuthor);
    return i < 0 ? 7 : i;
  }

  function renderLegend() {
    const target = $('#legend');
    if (view.mode === 'buddy') {
      target.innerHTML = `<span class="legend-item"><i class="swatch c0"></i>Source author</span><span class="legend-item"><i class="swatch c1"></i>Selected buddy</span><span class="legend-item"><i class="swatch overlap-swatch"></i>Shared papers</span><span class="legend-item"><i class="swatch context-swatch"></i>Other papers</span>`;
      return;
    }
    if (view.mode === 'add') {
      target.innerHTML = '<span class="legend-item"><i class="swatch overlap-swatch"></i>Selected paper</span><span class="legend-item"><i class="swatch c0"></i>Nearest papers</span><span class="legend-item"><i class="swatch c1"></i>Added in this browser</span><span class="legend-item"><i class="swatch context-swatch"></i>Other papers</span>';
      return;
    }
    if (view.mode === 'neighbors') {
      target.innerHTML = `<span class="legend-item"><i class="swatch overlap-swatch"></i>Selected paper A</span><span class="legend-item"><i class="swatch c0"></i>Nearest ${papers[state.selected].neighbors.length}</span>${state.comparison === null ? '' : '<span class="legend-item"><i class="swatch c1"></i>Comparison B</span>'}<span class="legend-item"><i class="swatch context-swatch"></i>Other papers</span>`;
      return;
    }
    if (view.mode === 'compare') {
      target.innerHTML = `<span class="legend-item"><i class="swatch c0"></i>A only · ${state.counts.a}</span><span class="legend-item"><i class="swatch c1"></i>B only · ${state.counts.b}</span><span class="legend-item"><i class="swatch overlap-swatch"></i>Both · ${state.counts.both}</span><span class="legend-item"><i class="swatch context-swatch"></i>Other papers</span>`;
      return;
    }
    if (view.mode === 'search') {
      if (!state.query) {target.innerHTML = '<span class="legend-item"><i class="swatch context-swatch"></i>All papers · search to highlight results</span>'; return;}
      target.innerHTML = state.kind === 'semantic'
        ? '<span class="legend-item">Lower similarity <i class="search-gradient"></i> Higher similarity</span><span class="legend-item"><i class="swatch context-swatch"></i>Other papers</span>'
        : '<span class="legend-item"><i class="swatch c0"></i>Matching papers</span><span class="legend-item"><i class="swatch context-swatch"></i>Other papers</span>';
      return;
    }
    let labels;
    if (view.color === 'year') labels = [...recentYears, `${Number(recentYears.at(-1)) - 1} & earlier`].map((label, i) => [label, i]);
    else if (view.color === 'branch') labels = [...topBranches.map((b, i) => [b, i]), ['Other / unlisted', 7]];
    else labels = [...topAuthors.map((a, i) => [a, i]), ['Other authors', 7]];
    target.innerHTML = labels.map(([label, i]) => `<span class="legend-item"><i class="swatch c${i}"></i>${escape(label)}</span>`).join('');
  }

  function project(point) {
    let x = point[0] * Math.cos(view.rotation) + point[2] * Math.sin(view.rotation);
    let z = -point[0] * Math.sin(view.rotation) + point[2] * Math.cos(view.rotation);
    const y = point[1] * Math.cos(view.tilt) - z * Math.sin(view.tilt);
    z = point[1] * Math.sin(view.tilt) + z * Math.cos(view.tilt);
    const perspective = 3.5 / (3.5 - z * .25);
    const scale = Math.min(width * .43, height * .44) * view.zoom * perspective;
    return [width * .5 + x * scale, height * .51 - y * scale, z];
  }

  function draw() {
    frame = null;
    if (!width || !height) return;
    context.clearRect(0, 0, width, height);
    context.lineWidth = .6;
    context.strokeStyle = '#92988b';
    context.globalAlpha = .19;
    for (let i = -4; i <= 4; i++) {
      const t = i / 4;
      for (const pair of [[[-1, -.83, t], [1, -.83, t]], [[t, -.83, -1], [t, -.83, 1]]]) {
        const a = project(pair[0]), b = project(pair[1]);
        context.beginPath(); context.moveTo(a[0], a[1]); context.lineTo(b[0], b[1]); context.stroke();
      }
    }
    projected = coords[view.projection].map((p, i) => ({i, p: project(p)})).sort((a, b) => a.p[2] - b.p[2]);
    const scores = state.results.map(r => r.similarity).filter(s => s !== undefined);
    const low = scores.length ? Math.min(...scores) : 0, high = scores.length ? Math.max(...scores) : 1;
    const nearest = ['neighbors', 'add'].includes(view.mode) ? new Set(papers[state.selected]?.neighbors.map(([index]) => index) || []) : null;
    for (const item of projected) {
      const matching = state.matches.has(item.i);
      let alpha = .73 + Math.max(-.2, Math.min(.2, item.p[2] * .15));
      if (view.mode === 'add') {
        context.fillStyle = item.i === state.selected ? '#111111' : nearest.has(item.i) ? palette[0] : papers[item.i].added ? palette[1] : palette[7];
        alpha = item.i === state.selected || nearest.has(item.i) || papers[item.i].added ? .98 : .09;
      } else if (view.mode === 'neighbors') {
        context.fillStyle = item.i === state.selected ? '#111111' : item.i === state.comparison ? palette[1] : nearest.has(item.i) ? palette[0] : palette[7];
        alpha = item.i === state.selected || nearest.has(item.i) ? .98 : .09;
      } else if (view.mode === 'compare' || view.mode === 'buddy') {
        const member = state.matches.get(item.i)?.membership;
        context.fillStyle = member === 'both' ? '#111111' : member === 'a' ? palette[0] : member === 'b' ? palette[1] : palette[7];
        alpha = member ? .95 : .1;
      } else if (view.mode === 'search' && state.query) {
        alpha = matching ? .95 : .09;
        if (state.kind === 'semantic' && matching) {
          const ratio = (state.matches.get(item.i) - low) / (high - low || 1);
          context.fillStyle = `rgb(${Math.round(135 - ratio * 67)},${Math.round(171 - ratio * 48)},${Math.round(184 - ratio * 30)})`;
        } else context.fillStyle = matching ? palette[0] : palette[7];
      } else if (view.mode === 'search') {context.fillStyle = palette[7]; alpha = .4;}
      else context.fillStyle = palette[category(papers[item.i])];
      context.globalAlpha = alpha;
      const emphasis = ['neighbors', 'add'].includes(view.mode) && (item.i === state.selected || nearest.has(item.i))
        || ['compare', 'buddy'].includes(view.mode) && state.matches.get(item.i)?.membership === 'both';
      context.beginPath(); context.arc(item.p[0], item.p[1], (emphasis ? 3.2 : 2.1) + (item.p[2] + 1) * .35, 0, Math.PI * 2); context.fill();
    }
    const a = projected.find(h => h.i === state.selected), b = view.mode === 'neighbors' ? projected.find(h => h.i === state.comparison) : null;
    context.strokeStyle = ink; context.fillStyle = ink;
    if (a && b) {
      context.globalAlpha = .4; context.lineWidth = 1; context.setLineDash([3, 4]);
      context.beginPath(); context.moveTo(a.p[0], a.p[1]); context.lineTo(b.p[0], b.p[1]); context.stroke(); context.setLineDash([]);
    }
    for (const [item, label] of [[a, 'A'], [b, 'B']]) {
      if (!item) continue;
      context.globalAlpha = 1; context.lineWidth = 1.3; context.beginPath(); context.arc(item.p[0], item.p[1], 7, 0, Math.PI * 2); context.stroke();
      const close = label === 'B' && a && Math.hypot(a.p[0] - item.p[0], a.p[1] - item.p[1]) < 30;
      if (view.mode === 'neighbors') {context.font = '12px Courier New'; context.fillText(label, item.p[0] + 11, item.p[1] + (close ? -14 : 4));}
    }
    context.globalAlpha = .7; context.lineWidth = 1; context.font = '10px Courier New';
    const origin = [29, height - 43];
    for (const [dx, dy, label] of [[22, 2, 'x'], [0, -23, 'y'], [-13, 10, 'z']]) {
      context.beginPath(); context.moveTo(...origin); context.lineTo(origin[0] + dx, origin[1] + dy); context.stroke();
      context.fillText(label, origin[0] + dx + 3, origin[1] + dy + 2);
    }
    context.globalAlpha = 1;
  }

  function scheduleDraw() {if (frame === null) frame = requestAnimationFrame(draw);}
  function resize() {
    const rect = $('#plot').getBoundingClientRect(); width = rect.width; height = rect.height;
    const ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0); scheduleDraw();
  }

  function renderNeighbors() {
    if (view.mode === 'buddy') {
      $('#list-heading').textContent = 'Paper buddies'; $('#neighbors').setAttribute('aria-label', 'Paper buddies');
      $('#list-subtitle').textContent = state.busy ? 'Comparing author portfolios…' : state.total ? `${state.total.toLocaleString()} ranked · top ${state.rankings.length}` : 'Closest bodies of work';
      $('#seed-ref').hidden = true; $('#set-filters').hidden = true; $('#list-value-heading').textContent = 'SINKHORN';
      $('#list-instructions').innerHTML = 'Lower divergence means closer research.<span>Equal paper weights · full embeddings</span>';
      $('#neighbor-list').innerHTML = state.rankings.map((row, rank) => `<button type="button" class="neighbor-row" data-buddy="${escape(row.name)}" aria-pressed="${row.name === state.buddy}" title="${escape(row.name)}. Sinkhorn divergence ${row.divergence.toFixed(6)}"><span class="citation">${rank + 1}. ${escape(row.name)}</span><span class="distance">${row.divergence.toFixed(4)}</span><span class="neighbor-title">${row.count} papers · ${row.shared} shared${row.divergence < 1e-10 ? ' · tied at zero' : ''}</span></button>`).join('') || `<p class="empty-state">${state.busy ? 'Solving transport between author portfolios…' : state.author ? 'No buddies match these settings. Try a lower paper minimum or include coauthors.' : 'Choose your name or another author in this collection to find a paper buddy.'}</p>`;
      return;
    }
    if (view.mode === 'add') {
      $('#list-heading').textContent = 'PMID lookups'; $('#neighbors').setAttribute('aria-label', 'PMID lookups');
      $('#list-subtitle').textContent = `${addedRecords.length} added in this browser`;
      $('#seed-ref').hidden = true; $('#set-filters').hidden = true; $('#list-value-heading').textContent = '';
      $('#list-instructions').innerHTML = 'Select a paper to highlight it.<span>Use Explore neighbors to compare papers.</span>';
      $('#neighbor-list').innerHTML = state.history.map(index => {
        const p = papers[index];
        return `<button type="button" class="neighbor-row" data-result="${index}" aria-pressed="${index === state.selected}" title="${escape(p.title)}"><span class="citation">${escape(citation(p))}</span><span class="lookup-tag">${p.added ? 'ADDED' : 'IN SPACE'}</span><span class="neighbor-title">${escape(p.title)}</span></button>`;
      }).join('') || '<p class="empty-state">Enter a PMID to place any PubMed paper in this landscape. Existing papers are highlighted immediately.</p>';
      return;
    }
    const neighborMode = view.mode === 'neighbors', compareMode = view.mode === 'compare';
    const heading = neighborMode ? 'Nearest papers' : compareMode ? 'Query comparison' : 'Search results';
    $('#list-heading').textContent = heading; $('#neighbors').setAttribute('aria-label', heading);
    $('#list-subtitle').textContent = neighborMode ? 'Cosine distance · lower is closer'
      : compareMode ? state.busy ? 'Finding papers for both searches…' : state.results.length ? `${state.counts.all} unique papers · ${state.counts.both} in both` : 'Compare ideas, authors, or branches'
      : state.busy ? 'Searching the collection…' : state.query ? `${state.results.length} ${state.kind === 'semantic' ? 'nearest' : 'matching'} papers` : 'Your ranked results appear here.';
    $('#seed-ref').hidden = !neighborMode;
    $('#set-filters').hidden = !compareMode || !state.results.length;
    $('#list-value-heading').textContent = neighborMode ? 'DISTANCE' : compareMode ? 'MATCH' : state.kind === 'semantic' ? 'SIMILARITY' : '';
    $('#list-instructions').innerHTML = (neighborMode ? 'Select a row to compare with A.' : 'Select a result to read the paper.')
      + '<span>Original 768-dimensional embeddings</span>';
    if (neighborMode) {
      const paper = papers[state.selected];
      $('#seed-citation').textContent = citation(paper);
      $('#neighbor-list').innerHTML = paper.neighbors.map(([i, distance]) => `<button type="button" class="neighbor-row" data-neighbor="${i}" aria-pressed="${i === state.comparison}" title="${escape(papers[i].title)}" aria-label="Compare with ${escape(papers[i].title)}. Cosine distance ${distance.toFixed(3)}"><span class="citation">${escape(citation(papers[i]))}</span><span class="distance">${distance.toFixed(3)}</span><span class="neighbor-title">${escape(papers[i].title)}</span></button>`).join('');
      return;
    }
    $$('[data-set-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.setFilter === state.filter)));
    $$('[data-set-count]').forEach(span => {span.textContent = state.counts[span.dataset.setCount];});
    const results = compareMode && state.filter !== 'all' ? state.results.filter(r => r.membership === state.filter) : state.results;
    $('#neighbor-list').innerHTML = results.map(result => {
      const p = papers[result.index];
      const value = compareMode ? `<span class="membership membership-${result.membership}">${result.membership === 'both' ? 'BOTH' : result.membership.toUpperCase()}</span>` : result.similarity === undefined ? '' : result.similarity.toFixed(3);
      return `<button type="button" class="neighbor-row ${compareMode ? 'set-' + result.membership : ''}" data-result="${result.index}" aria-pressed="${result.index === state.selected}" title="${escape(p.title)}"><span class="citation">${escape(citation(p))}</span><span class="distance">${value}</span><span class="neighbor-title">${escape(p.title)}</span></button>`;
    }).join('') || `<p class="empty-state">${state.busy ? 'Finding papers…' : compareMode ? state.results.length ? 'No papers in this set.' : 'Enter two ideas above to compare their research neighborhoods.' : state.query ? 'No matching papers.' : 'Search an idea, researcher, or branch to see results here.'}</p>`;
  }

  function renderPapers() {
    const buddyCard = view.mode === 'buddy' ? renderBuddyCard() : '';
    const pair = view.mode === 'neighbors' && state.comparison !== null;
    $('#inspector').classList.toggle('comparing', pair);
    $('#detail-count').textContent = state.selected === null ? 'SELECT A PAPER' : pair ? 'COMPARING 2 ↓' : '1 PAPER';
    $('#compare-bar').hidden = !pair;
    if (state.selected === null) {$('#paper-scroll').innerHTML = buddyCard || '<p class="empty-state">Select a paper from the plot or the results list to read its text and metadata.</p>'; return;}
    const distance = pair ? PaperSpace.distance(corpus, data.dimensions, state.selected,
      corpus.subarray(state.comparison * data.dimensions, (state.comparison + 1) * data.dimensions)) : null;
    $('#compare-distance').textContent = distance == null ? '' : `${distance.toFixed(3)} distance`;
    const chosen = pair ? [state.selected, state.comparison] : [state.selected];
    $('#paper-scroll').innerHTML = buddyCard + chosen.map((index, position) => {
      const p = papers[index], full = state.expandedPapers.has(index), limit = pair ? 120 : 620;
      const excerpt = p.abstract ? full ? p.abstract : p.abstract.slice(0, limit) + (p.abstract.length > limit ? '…' : '')
        : 'No abstract was supplied. This paper was embedded using its title only.';
      const authorLimit = pair ? 2 : 7;
      const authors = p.authors.slice(0, authorLimit).join('; ') + (p.authors.length > authorLimit ? ' et al.' : '');
      const publicationURL = safeURL(p.url) || (p.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(p.pmid)}/` : p.doi ? `https://doi.org/${encodeURI(p.doi)}` : '');
      const metadata = [
        ['Published', p.date || 'Not supplied'], ['Authors', p.authors.join('; ')],
        ['Journal', p.journal || 'Not supplied'],
        ['Branches', p.branches.length ? p.branches.map(b => branchNames.get(b) || b).join('; ') : 'Not listed in publication metadata'],
        ['DOI', p.doi || 'Not supplied'], ['PMID', p.pmid || 'Not supplied'],
        ['Publication type', p.types || 'Not supplied'], ['Source tags', p.tags || 'Not supplied'],
        ['Embedding', `EmbeddingGemma 2 · ${p.abstract ? 'title + abstract' : 'title only'} · 768 dimensions`],
        ...(p.added ? [['Source', 'PubMed · added in this browser'], ['New-paper position', view.projection === 'pca' ? 'Original fitted PCA transform' : 'Estimated from nearest base papers; existing layout is fixed'], ['Browser embedding', 'Quantized version of the base model; rankings may differ slightly from full precision']] : []),
      ];
      const membership = view.mode === 'compare' ? state.matches.get(index) : null;
      const scoreLabel = side => state['querySpec' + side]?.kind === 'semantic'
        ? membership['rank' + side] === null ? 'outside top 50' : membership['similarity' + side].toFixed(3)
        : membership['rank' + side] === null ? 'not matched' : 'exact match';
      const scores = membership ? `<div class="query-scores"><span class="membership membership-${membership.membership}">${membership.membership === 'both' ? 'BOTH QUERIES' : 'QUERY ' + membership.membership.toUpperCase() + ' ONLY'}</span><span>A: ${scoreLabel('A')}</span><span>B: ${scoreLabel('B')}</span></div>` : '';
      const letter = view.mode === 'neighbors' ? `<span class="letter ${position ? 'b' : ''}">${position ? 'B' : 'A'}</span>` : '';
      const placement = p.added ? `<p class="new-paper-note">Added in this browser · ${view.projection === 'pca' ? 'fitted PCA position' : 'estimated ' + (view.projection === 'umap' ? 'UMAP' : 't-SNE') + ' position'}. Neighbor distances use the full embedding.</p>` : '';
      return `<article class="paper-entry" data-paper-index="${index}" aria-label="${view.mode === 'neighbors' ? 'Paper ' + (position ? 'B' : 'A') + ': ' : ''}${escape(p.title)}"><div class="entry-label">${letter}<span>${position ? 'COMPARISON' : 'SELECTED PAPER'}</span>${position ? '<button type="button" data-make-anchor class="text-button">Make A ↗</button>' : ''}</div><div class="paper-meta"><span class="document-type">${p.types.includes('Review') ? 'REVIEW' : 'PAPER'}</span><span>${escape(p.year)}</span></div><h2>${escape(p.title)}</h2><div class="authors">${escape(authors)}</div><div class="journal">${escape(p.journal || 'Journal not supplied')}</div>${placement}${scores}<div class="paper-text" ${state.tab === 'text' ? '' : 'hidden'}><p class="embedded-label">${p.abstract ? 'TITLE ABOVE + ABSTRACT BELOW' : 'TITLE ONLY'}</p><p class="abstract">${escape(excerpt)}</p>${p.abstract.length > limit ? `<button type="button" class="text-button" data-expand-paper="${index}">${full ? 'Collapse abstract ↑' : 'Read full abstract ↓'}</button>` : ''}</div><dl class="metadata" ${state.tab === 'meta' ? '' : 'hidden'}>${metadata.map(([key, value]) => `<dt>${escape(key)}</dt><dd>${escape(value)}</dd>`).join('')}</dl>${publicationURL ? `<a class="paper-link" href="${escape(publicationURL)}" target="_blank" rel="noopener">View publication <span aria-hidden="true">↗</span></a>` : ''}</article>`;
    }).join('');
  }

  function select(index) {
    const scroll = $('#neighbor-list').scrollTop;
    state.selected = index; state.comparison = null; state.expandedPapers.clear();
    renderNeighbors(); renderPapers(); renderLegend(); $('#paper-scroll').scrollTop = 0; $('#neighbor-list').scrollTop = view.mode === 'neighbors' ? 0 : scroll;
    $('#plot-tooltip').hidden = true; scheduleDraw(); persist();
    if (view.mode === 'add') renderStatus();
  }

  function compare(index) {
    if (view.mode !== 'neighbors') {select(index); return;}
    state.comparison = index; state.expandedPapers.clear();
    renderNeighbors(); renderPapers(); renderLegend(); $('#paper-scroll').scrollTop = 0; scheduleDraw();
  }

  function criterion(kind, value) {
    return {kind, value, label: kind === 'branch' ? branchNames.get(value) || value : value};
  }

  function setCompareChoice(field, choice) {
    state[field === 'search' ? 'choiceA' : 'choiceB'] = choice;
    state[field === 'search' ? 'draft' : 'draftB'] = choice.label;
    $('#' + field).value = choice.label;
    renderQueryTypes();
  }

  function renderQueryTypes() {
    for (const side of ['A', 'B']) {
      const label = $('#query-type-' + side.toLowerCase());
      label.hidden = view.mode !== 'compare';
      label.textContent = ({semantic: 'Meaning', researcher: 'Person', group: 'Group', author: 'Author', branch: 'Branch'})[state['choice' + side]?.kind || 'semantic'];
      if (view.mode === 'compare') $(side === 'A' ? '#search' : '#search-b').setAttribute('aria-label', `Query ${side} · ${label.textContent}`);
    }
  }

  function exactResults(kind, value) {
    const index = kind === 'branch' ? branchIndex : authorIndex;
    return (index.get(value) || []).map(i => ({index: i}));
  }

  function showSuggestions(field = 'search') {
    hideSuggestions();
    const input = $('#' + field), text = input.value.trim();
    if (!text) return;
    activeCompareField = field;
    const target = $('#' + input.getAttribute('aria-controls'));
    const key = normalize(text);
    const branchMatches = data.branches.filter(b => normalize(b.name + ' ' + b.code).includes(key)).slice(0, 3);
    const authors = PaperAutocomplete.authorSuggestions(authorIndex, data.authorTypes || {}, text);
    const demo = data.queries.some(q => normalize(q.text) === key);
    const option = (kind, value, label, subtitle) => `<button type="button" class="suggestion" data-search-kind="${kind}" data-value="${escape(value)}"><span class="kind">${({semantic: 'SEMANTIC', branch: 'BRANCH', researcher: 'PERSON', group: 'GROUP', author: 'AUTHOR'})[kind]}</span><span class="suggest-text">${escape(label)}<small>${escape(subtitle)}</small></span></button>`;
    target.innerHTML = option('semantic', text, `Explore “${text}”`, demo ? 'Search by meaning · ready offline' : 'Search by meaning · runs locally in your browser')
      + branchMatches.map(b => option('branch', b.code, b.name, `${branchIndex.get(b.code)?.length || 0} papers with a branch annotation`)).join('')
      + authors.map(a => option(a.kind, a.name, a.name, `${authorIndex.get(a.name).length} papers · ${a.kind === 'group' ? 'match this collective author' : 'match this published name'}`)).join('')
      + (!demo ? '<div class="suggestion-note">First custom search downloads the browser model (~230 MB).</div>' : '');
    target.hidden = false; input.setAttribute('aria-expanded', 'true');
  }

  function renderStatus() {
    const addMode = view.mode === 'add', importState = states.add;
    $('#pmid-submit').disabled = importState.busy;
    $('#pmid-submit').textContent = importState.busy ? 'Adding…' : 'Add to space ↗';
    $('#pmid').disabled = importState.busy;
    $('#pmid-status').hidden = !addMode || !(importState.notice || importState.progress || importState.storageWarning);
    $('#pmid-message').textContent = [importState.busy ? importState.progress : importState.notice, importState.storageWarning].filter(Boolean).join(' ');
    $('#pmid-neighbors').hidden = importState.selected === null || importState.busy;
    const compareMode = view.mode === 'compare';
    $('#buddy-status').hidden = view.mode !== 'buddy' || !state.progress;
    $('#buddy-status').textContent = states.buddy.progress;
    $('#buddy-submit').textContent = states.buddy.busy ? 'Comparing…' : 'Find paper buddy ↗';
    $('#buddy-cancel').hidden = !states.buddy.busy;
    $('#search-state').hidden = addMode || view.mode === 'buddy' || (!state.query && !state.busy);
    $('#search-label').textContent = compareMode ? 'Query A + Query B'
      : `${({semantic: view.mode === 'neighbors' ? 'Find starting paper' : 'Semantic search', branch: 'Branch', researcher: 'Researcher', group: 'Group author', author: 'Author'})[state.kind]} / ${state.kind === 'branch' ? branchNames.get(state.query) || state.query : state.query}`;
    $('#search-progress').textContent = state.progress;
    $('#search-submit').textContent = state.busy ? compareMode ? 'Comparing…' : 'Searching…' : compareMode ? 'Compare ↗' : 'Search ↗';
    $('#clear-search').textContent = compareMode ? 'Clear queries ×' : 'Clear search ×';
    $('#show-results').hidden = !state.query || state.busy || !!state.error;
    $('#show-results').textContent = compareMode ? `${state.counts.all} unique · ${state.counts.both} overlap`
      : `${state.results.length}${state.kind === 'semantic' ? ' nearest' : ' matching'} papers${view.mode === 'neighbors' ? ' ▾' : ''}`;
    $('#show-results').setAttribute('aria-controls', view.mode === 'neighbors' ? 'search-results' : 'neighbor-list');
    if (view.mode !== 'neighbors') $('#show-results').removeAttribute('aria-expanded');
    $('#model-status').classList.toggle('busy', state.busy);
    $('#error').hidden = !state.error; $('#error span').textContent = state.error;
    if (view.mode === 'neighbors') $('#search-results').innerHTML = state.results.map(r => `<button type="button" class="result-row" data-result="${r.index}"><span>${escape(papers[r.index].title)}</span><small>${escape(citation(papers[r.index]))}</small>${r.similarity === undefined ? '' : `<span class="result-score">${r.similarity.toFixed(3)}<small>similarity</small></span>`}</button>`).join('') || '<p class="empty-state">No matching papers.</p>';
  }

  function renderMode() {
    const compareMode = view.mode === 'compare', neighborMode = view.mode === 'neighbors', addMode = view.mode === 'add', buddyMode = view.mode === 'buddy';
    $('#app').dataset.mode = view.mode;
    $$('[data-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === view.mode)));
    $('#mode-hint').textContent = buddyMode ? 'Match authors by the shape of their research.' : addMode ? 'Bring any PubMed paper into the landscape.' : neighborMode ? 'Selected paper and nearest neighbors control the colors.'
      : compareMode ? 'Compare ideas, authors, groups, or branches · shared papers in black.' : 'Find papers by meaning, researcher, or branch.';
    $('#query-a-label').hidden = !compareMode; $('#query-b-wrap').hidden = !compareMode;
    $('#search-form').hidden = addMode || buddyMode; $('#pmid-form').hidden = !addMode; $('#buddy-form').hidden = !buddyMode;
    $('.search-hint:not(#pmid-hint)').hidden = addMode || buddyMode; $('#pmid-hint').hidden = !addMode;
    $('#buddy-author').value = states.buddy.authorDraft;
    $('#pmid').value = states.add.pmid;
    $('#search').placeholder = compareMode ? 'First idea, author, or branch…' : neighborMode ? 'Find a starting paper, researcher, or branch…' : 'Explore an idea, researcher, or branch…';
    $('#search').setAttribute('aria-label', compareMode ? 'Query A' : neighborMode ? 'Find a starting paper' : 'Search papers, researchers, or branches');
    $('#search').value = state.draft; $('#search-b').value = state.draftB;
    $('#color-by').hidden = true; $('#color-label').hidden = true;
    $('#color-by').disabled = neighborMode; $('#mode-color').hidden = false;
    $('#mode-color').textContent = buddyMode ? 'Author portfolios' : addMode ? 'Selected + added papers' : neighborMode ? 'Nearest neighbors' : compareMode ? 'Query membership' : 'Search relevance';
    renderQueryTypes();
    $$('[data-tab]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.tab === state.tab)));
    renderStatus(); renderLegend(); renderNeighbors(); renderPapers(); scheduleDraw();
  }

  function switchMode(next) {
    if (next === view.mode || !states[next]) return;
    state.draft = $('#search').value; state.draftB = $('#search-b').value;
    state.listScroll = $('#neighbor-list').scrollTop; state.paperScroll = $('#paper-scroll').scrollTop;
    hideSuggestions(); $('#search-results').hidden = true; $('#plot-tooltip').hidden = true;
    view.mode = next; state = states[next];
    renderMode(); $('#neighbor-list').scrollTop = state.listScroll; $('#paper-scroll').scrollTop = state.paperScroll; persist();
  }

  function updateContext(context) {
    if (context !== state) return;
    renderStatus(); renderNeighbors(); renderLegend(); renderPapers(); scheduleDraw();
  }

  function applySearchResults(context, results, precomputed = false) {
    context.results = results; context.matches = new Map(results.map(r => [r.index, r.similarity ?? 1]));
    context.busy = false; context.progress = precomputed ? 'Precomputed query · local ranking' : '';
    if (!context.preserveSelection) {
      context.selected = results.length ? results[0].index : context === states.neighbors ? context.selected : null;
      context.comparison = null; context.expandedPapers.clear();
    }
    updateContext(context);
  }

  function applyComparisonResults(context) {
    if (context.resultsA === null || context.resultsB === null || context.error) return;
    const combined = PaperModes.combineQueryResults(context.resultsA, context.resultsB,
      {a: context.querySpecA.kind === 'semantic' ? 50 : Infinity, b: context.querySpecB.kind === 'semantic' ? 50 : Infinity});
    context.results = combined.results; context.counts = combined.counts;
    context.matches = new Map(context.results.map(r => [r.index, r]));
    if (!context.preserveSelection || !context.matches.has(context.selected)) context.selected = context.results[0]?.index ?? null;
    context.busy = false; context.progress = ['A', 'B'].map(side => `${side}: ${context['querySpec' + side].kind === 'semantic' ? 'top 50' : context['results' + side].length + ' exact matches'}`).join(' · ');
    updateContext(context);
  }

  function ensureWorker() {
    if (worker) return worker;
    const blob = new Blob([$('#search-worker-source').textContent], {type: 'text/javascript'});
    const url = URL.createObjectURL(blob);
    worker = new Worker(url); URL.revokeObjectURL(url);
    worker.onmessage = ({data: message}) => {
      if (message.type === 'ready') {$('#model-status').textContent = `Local search ready · ${message.device === 'webgpu' ? 'GPU' : 'CPU'}`; return;}
      const job = jobs.get(message.requestId);
      if (!job || job.context.version !== job.version) return;
      const context = job.context;
      if (message.type === 'progress') {
        context.progress = (job.side ? 'Query ' + job.side + ': ' : '') + message.text;
        if (context === state) renderStatus();
      }
      if (message.type === 'error') {
        jobs.delete(message.requestId); context.busy = false;
        context.error = (job.side ? 'Query ' + job.side + ': ' : '') + message.text;
        context.progress = context === states.add ? '' : 'Search unavailable'; updateContext(context);
      }
      if (message.type === 'results') {
        jobs.delete(message.requestId);
        if (job.side) {context['results' + job.side] = message.results; applyComparisonResults(context);}
        else applySearchResults(context, message.results, message.precomputed);
      }
      if (message.type === 'paper-vector') {
        jobs.delete(message.requestId);
        try {
          const index = installPaper(job.record, message.vector);
          rememberLookup(index); saveAdditions();
          context.busy = false; context.progress = '';
          context.notice = `PMID ${papers[index].pmid} added to the space.${papers[index].abstract ? '' : ' No abstract is available; embedded using its title only.'}`;
          updateCollectionLabels(); refreshCollectionResults(); updateContext(state);
        } catch (cause) {context.busy = false; context.error = cause.message; updateContext(context);}
      }
    };
    worker.onerror = event => {
      for (const {context, version} of jobs.values()) {
        if (context.version !== version) continue;
        context.busy = false; context.progress = 'Search unavailable';
        context.error = 'The browser embedding worker could not start. Please retry. ' + event.message;
      }
      jobs.clear(); worker.terminate(); worker = null; updateContext(state);
    };
    worker.postMessage({type: 'init', dimensions: data.dimensions, count: baseCount, vectors: data.vectors, contextTokens: data.contextTokens,
      queries: data.queries, settings: data.browserModel, runtimeSource: $('#browser-runtime-source').textContent});
    for (const record of addedRecords) worker.postMessage({type: 'append', vector: record.vector});
    return worker;
  }

  function invalidate(context) {
    context.version++;
    for (const [id, job] of jobs) if (job.context === context) jobs.delete(id);
  }

  function requestQuery(context, query, side = null) {
    const id = ++requestCounter;
    jobs.set(id, {context, version: context.version, side});
    try {ensureWorker().postMessage({type: 'search', requestId: id, query, limit: 50});}
    catch (cause) {jobs.delete(id); context.busy = false; context.error = cause.message; updateContext(context);}
  }

  function search(kind, query) {
    query = query.trim(); if (!query) return;
    if (view.mode === 'compare') {setCompareChoice('search', criterion(kind, query)); runComparison(); return;}
    hideSuggestions(); $('#search-results').hidden = true; $('#show-results').setAttribute('aria-expanded', 'false');
    invalidate(state); state.preserveSelection = false; state.query = query; state.draft = query; state.kind = kind;
    state.results = []; state.matches.clear(); state.error = ''; state.progress = 'Finding papers…';
    state.comparison = null; state.expandedPapers.clear();
    if (view.mode !== 'neighbors') state.selected = null;
    $('#search').value = query;
    if (kind === 'semantic') {state.busy = true; updateContext(state); requestQuery(state, query);}
    else applySearchResults(state, exactResults(kind, query));
    $('#paper-scroll').scrollTop = 0; $('#neighbor-list').scrollTop = 0;
  }

  function runComparison() {
    const a = $('#search').value.trim(), b = $('#search-b').value.trim();
    state.draft = a; state.draftB = b;
    if (!a || !b) {invalidate(state); state.busy = false; state.progress = ''; error('Enter both query A and query B to compare their matching papers.'); renderStatus(); (!a ? $('#search') : $('#search-b')).focus(); return;}
    const specA = state.choiceA?.label === a ? {...state.choiceA} : criterion('semantic', a);
    const specB = state.choiceB?.label === b ? {...state.choiceB} : criterion('semantic', b);
    hideSuggestions(); invalidate(state);
    Object.assign(state, {query: a, queryB: b, kind: 'semantic', results: [], resultsA: null, resultsB: null,
      querySpecA: specA, querySpecB: specB, preserveSelection: false, selected: null, comparison: null, filter: 'all', busy: true, error: '', progress: 'Finding papers for both searches…',
      counts: {all: 0, a: 0, b: 0, both: 0}});
    state.matches.clear(); state.expandedPapers.clear(); updateContext(state);
    for (const [side, spec] of [['A', specA], ['B', specB]]) {
      if (spec.kind === 'semantic') requestQuery(state, spec.value, side);
      else state['results' + side] = exactResults(spec.kind, spec.value);
    }
    applyComparisonResults(state);
    $('#paper-scroll').scrollTop = 0; $('#neighbor-list').scrollTop = 0;
  }

  function clearSearch() {
    invalidate(state);
    Object.assign(state, {query: '', queryB: '', draft: '', draftB: '', results: [], resultsA: null, resultsB: null,
      choiceA: null, choiceB: null, querySpecA: null, querySpecB: null,
      filter: 'all', busy: false, progress: '', error: '', counts: {all: 0, a: 0, b: 0, both: 0}});
    state.matches.clear();
    if (view.mode !== 'neighbors') {state.selected = null; state.comparison = null;}
    $('#search').value = ''; $('#search-b').value = ''; $('#search-results').hidden = true;
    $('#show-results').setAttribute('aria-expanded', 'false'); hideSuggestions(); renderQueryTypes(); updateContext(state);
  }

  function renderBuddyCard() {
    if (!state.author) return '<section class="buddy-card"><p class="eyebrow">FIND YOUR PAPER BUDDY</p><h2>Who shares your research landscape?</h2><p>Choose an author above. We compare the distributions of their papers using debiased Sinkhorn divergence.</p><p>Matches use papers in this collection. Author names are matched as published.</p></section>';
    const source = authorIndex.get(state.author) || [], buddy = state.rankings.find(r => r.name === state.buddy);
    const portfolio = (name, indices, open = false) => `<details ${open ? 'open' : ''}><summary>${escape(name)} · ${indices.length} papers</summary><div class="buddy-paper-list">${indices.map(index => `<button type="button" data-buddy-paper="${index}" aria-pressed="${state.selected === index}">${escape(papers[index].title)} · ${escape(papers[index].year)}</button>`).join('')}</div></details>`;
    return `<section class="buddy-card"><p class="eyebrow">${buddy ? 'PAPER BUDDY' : 'CHOSEN AUTHOR'}</p><h2>${escape(buddy?.name || state.author)}</h2>${buddy ? `<p>Closest portfolio to ${escape(state.author)} under these settings.</p><p class="buddy-score">Sinkhorn divergence ${buddy.divergence.toFixed(6)}<br>${source.length} source papers · ${buddy.count} buddy papers · ${buddy.shared} shared</p>` : `<p>${source.length} papers in this collection.</p>`}<p>Equal paper weights · cosine cost · ε = ${state.epsilon}. Lower is closer.${source.length < 3 || buddy?.count < 3 ? ' Sparse portfolios provide limited evidence.' : ''}</p>${portfolio(state.author, source)}${buddy ? portfolio(buddy.name, authorIndex.get(buddy.name), true) : ''}<p>Profiles cover this collection; published names may refer to more than one person.</p></section>`;
  }

  function showBuddySuggestions() {
    hideSuggestions();
    const query = $('#buddy-author').value;
    if (!query.trim()) return;
    const index = new Map([...authorIndex].filter(([name]) => data.authorTypes[name] !== 'group'));
    const authors = PaperAutocomplete.authorSuggestions(index, data.authorTypes, query, 8);
    $('#buddy-suggestions').innerHTML = authors.map(a => `<button type="button" class="suggestion" data-buddy-author="${escape(a.name)}"><span class="kind">${a.kind === 'researcher' ? 'PERSON' : 'AUTHOR'}</span><span class="suggest-text">${escape(a.name)}<small>${authorIndex.get(a.name).length} papers in this collection</small></span></button>`).join('') || '<p class="suggestion-note">No matching individual author in this collection. Add a paper by PMID to include its authors.</p>';
    $('#buddy-suggestions').hidden = false; $('#buddy-author').setAttribute('aria-expanded', 'true');
  }

  function colorBuddy(context) {
    const a = (authorIndex.get(context.author) || []).map(index => ({index}));
    const b = (authorIndex.get(context.buddy) || []).map(index => ({index}));
    context.results = PaperModes.combineQueryResults(a, b, Infinity).results;
    context.matches = new Map(context.results.map(r => [r.index, r]));
  }

  function selectBuddy(name, context = states.buddy) {
    if (!context.rankings.some(r => r.name === name)) return;
    context.buddy = name; colorBuddy(context);
    const source = new Set(authorIndex.get(context.author));
    const indices = authorIndex.get(name);
    context.selected = indices.find(index => !source.has(index)) ?? indices[0];
    context.expandedPapers.clear(); updateContext(context);
    if (context === state) $('#paper-scroll').scrollTop = 0;
  }

  function stopBuddy() {
    states.buddy.version++;
    if (buddyWorker) {buddyWorker.terminate(); buddyWorker = null;}
    states.buddy.busy = false;
  }

  function findBuddy(authorOverride = null) {
    const context = states.buddy;
    const draft = authorOverride ?? $('#buddy-author').value.trim();
    const matches = authorIndex.has(draft) && data.authorTypes[draft] !== 'group' ? [draft]
      : [...authorIndex.keys()].filter(name => data.authorTypes[name] !== 'group' && normalize(name) === normalize(draft));
    if (context.busy) stopBuddy();
    if (matches.length !== 1) {
      context.error = 'Choose an individual author from the suggestions, using their full published name.';
      updateContext(context); return;
    }
    const author = matches[0];
    Object.assign(context, {author, authorDraft: author, buddy: null, rankings: [], total: 0, selected: null,
      busy: true, error: '', progress: 'Preparing author portfolios…', minimumPapers: Number($('#buddy-minimum').value),
      epsilon: Number($('#buddy-epsilon').value), excludeCoauthors: $('#buddy-exclude').checked});
    context.version++; context.expandedPapers.clear(); colorBuddy(context); hideSuggestions();
    $('#buddy-author').value = author; updateContext(context);
    try {
      if (!buddyWorker) {
        const blob = new Blob([$('#buddy-math-source').textContent, '\n', $('#buddy-worker-source').textContent], {type: 'text/javascript'});
        const url = URL.createObjectURL(blob);
        buddyWorker = new Worker(url, {name: 'paper-buddy'}); URL.revokeObjectURL(url);
        const currentWorker = buddyWorker;
        buddyWorker.onmessage = ({data: message}) => {
          if (currentWorker !== buddyWorker || message.requestId !== context.version) return;
          if (message.type === 'buddy-progress') {context.progress = message.text; if (context === state) renderStatus();}
          if (message.type === 'buddy-error') {context.busy = false; context.error = message.text; context.progress = ''; updateContext(context);}
          if (message.type === 'buddies') {
            context.busy = false; context.rankings = message.results; context.total = message.total;
            context.progress = `${message.sourceCount} source papers · ${message.total.toLocaleString()} candidates ranked${message.excluded ? ' · ' + message.excluded + ' coauthors excluded' : ''}${message.failed ? ' · ' + message.failed + ' candidates omitted because transport did not converge' : ''}`;
            if (message.results.length) selectBuddy(message.results[0].name, context);
            else {colorBuddy(context); updateContext(context);}
          }
        };
        buddyWorker.onerror = event => {
          if (currentWorker !== buddyWorker) return;
          stopBuddy(); context.error = 'Paper buddy matching could not run. Please retry. ' + event.message;
          context.progress = ''; updateContext(context);
        };
        buddyWorker.postMessage({type: 'buddy-init', vectors: data.vectors, costs: data.buddyCosts, dimensions: data.dimensions,
          baseCount, additions: addedRecords.map(r => r.vector), papers: papers.map(p => ({authors: p.authors})), authorTypes: data.authorTypes});
      }
      buddyWorker.postMessage({type: 'buddy-find', requestId: context.version, author, epsilon: context.epsilon,
        minimumPapers: context.minimumPapers, excludeCoauthors: context.excludeCoauthors});
    } catch (cause) {stopBuddy(); context.error = cause.message; context.progress = ''; updateContext(context);}
  }

  function installPaper(record, vector) {
    const p = record.paper;
    if (!p || PaperPubMed.normalizePMID(p.pmid) !== p.pmid || p.id !== 'pmid:' + p.pmid || !p.title?.trim()
      || !['title', 'abstract', 'firstAuthor', 'date', 'year', 'journal', 'types', 'tags'].every(key => typeof p[key] === 'string')
      || !Array.isArray(p.authors) || p.authors.some(a => typeof a !== 'string')) throw new Error('Invalid saved PubMed paper.');
    if (papers.some(paper => paper.pmid === p.pmid)) throw new Error('This PMID is already in the space.');
    const plan = PaperSpace.plan(vector, corpus, data.dimensions, data.coordinates, data.pcaTransform, baseCount);
    const index = papers.length;
    const paper = {...p, added: true, branches: [], neighbors: plan.neighbors};
    // Plan and validate before committing any changes to the shared collection.
    const updates = plan.ranked.map(r => [r.index, [...papers[r.index].neighbors, [index, r.distance]]
      .sort((a, b) => a[1] - b[1] || a[0] - b[0]).slice(0, 10)]);
    for (const [i, neighbors] of updates) papers[i].neighbors = neighbors;
    corpus = plan.corpus; papers.push(paper); byId.set(paper.id, index);
    for (const a of new Set(paper.authors)) {
      if (!authorIndex.has(a)) authorIndex.set(a, []);
      authorIndex.get(a).push(index);
      const kind = record.authorTypes?.[a];
      if (kind === 'group' || (kind === 'person' && !data.authorTypes[a])) data.authorTypes[a] = kind;
    }
    for (const [method, point] of Object.entries(plan.positions)) {
      data.coordinates[method].push(point);
      const {center, scale} = coordinateFrames[method];
      coords[method].push(point.map((v, axis) => (v - center[axis]) / scale));
    }
    addedRecords.push({paper, vector: Array.from(plan.vector), authorTypes: record.authorTypes || {}});
    if (worker) {
      try {worker.postMessage({type: 'append', vector: plan.vector});}
      catch {worker.terminate(); worker = null;}
    }
    return index;
  }

  function rememberLookup(index) {
    const target = states.add;
    target.selected = index; target.expandedPapers.clear();
    target.history = [index, ...target.history.filter(i => i !== index)];
    if (view.mode === 'add' && width && height) {
      const [x, y] = project(coords[view.projection][index]);
      const fit = Math.min(1, Math.max(1, width / 2 - 50) / Math.max(1, Math.abs(x - width / 2)),
        Math.max(1, height / 2 - 65) / Math.max(1, Math.abs(y - height / 2)));
      view.zoom *= fit;
    }
  }

  function saveAdditions() {
    try {localStorage.setItem(storageKey, JSON.stringify(addedRecords)); states.add.storageWarning = '';}
    catch {states.add.storageWarning = 'Added for this session; browser storage is unavailable or full.';}
  }

  function updateCollectionLabels() {
    $('#plot-subtitle').textContent = `${papers.length.toLocaleString()} PAPERS · TITLE + ABSTRACT`;
    $('#data-status').textContent = `${baseCount.toLocaleString()} base papers${addedRecords.length ? ' + ' + addedRecords.length + ' added' : ''} · local data`;
  }

  function refreshCollectionResults() {
    stopBuddy();
    if (states.buddy.author) findBuddy(states.buddy.author);
    for (const context of [states.search, states.neighbors]) {
      if (!context.query) continue;
      invalidate(context); context.preserveSelection = true; context.error = '';
      if (context.kind === 'semantic') {context.busy = true; requestQuery(context, context.query);}
      else applySearchResults(context, exactResults(context.kind, context.query));
    }
    const context = states.compare;
    if (!context.querySpecA || !context.querySpecB) return;
    invalidate(context); context.preserveSelection = true; context.error = ''; context.busy = true;
    context.resultsA = null; context.resultsB = null;
    for (const side of ['A', 'B']) {
      const spec = context['querySpec' + side];
      if (spec.kind === 'semantic') requestQuery(context, spec.value, side);
      else context['results' + side] = exactResults(spec.kind, spec.value);
    }
    applyComparisonResults(context);
  }

  async function addPMID() {
    const context = states.add;
    if (context.busy) return;
    let pmid;
    context.error = ''; context.notice = ''; context.progress = '';
    try {pmid = PaperPubMed.normalizePMID($('#pmid').value);}
    catch (cause) {context.error = cause.message; updateContext(context); return;}
    context.pmid = pmid; $('#pmid').value = pmid;
    const existing = papers.findIndex(p => String(p.pmid) === pmid || p.id === 'pmid:' + pmid);
    if (existing !== -1) {
      rememberLookup(existing); context.notice = `PMID ${pmid} is already in the space. Its paper is highlighted.`;
      updateContext(context); return;
    }
    invalidate(context); const version = context.version;
    context.busy = true; context.progress = `Looking up PMID ${pmid} in PubMed…`; updateContext(context);
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 25000);
    let record;
    try {record = await PaperPubMed.lookup(pmid, {signal: controller.signal});}
    catch (cause) {
      context.busy = false; context.progress = '';
      context.error = cause.name === 'AbortError' ? 'PubMed lookup timed out. Please try again.' : cause instanceof TypeError ? 'Could not reach PubMed. Check your internet connection and try again.' : cause.message;
      updateContext(context); return;
    } finally {clearTimeout(timeout);}
    if (context.version !== version) return;
    const id = ++requestCounter;
    context.progress = 'PubMed paper found · preparing its local embedding…'; updateContext(context);
    jobs.set(id, {context, version, record});
    try {ensureWorker().postMessage({type: 'embed-paper', requestId: id, title: record.paper.title, abstract: record.paper.abstract});}
    catch (cause) {jobs.delete(id); context.busy = false; context.error = cause.message; updateContext(context);}
  }

  $('#pmid-form').addEventListener('submit', event => {event.preventDefault(); addPMID();});
  $('#pmid').addEventListener('input', () => {states.add.pmid = $('#pmid').value;});
  $('#pmid-neighbors').addEventListener('click', () => {const index = states.add.selected; if (index !== null) {switchMode('neighbors'); select(index);}});
  $('#buddy-form').addEventListener('submit', event => {event.preventDefault(); findBuddy();});
  $('#buddy-author').addEventListener('input', () => {states.buddy.authorDraft = $('#buddy-author').value; showBuddySuggestions();});
  $('#buddy-author').addEventListener('focus', showBuddySuggestions);
  $('#buddy-author').addEventListener('keydown', event => {
    if (event.key === 'ArrowDown' && !$('#buddy-suggestions').hidden) {event.preventDefault(); $('#buddy-suggestions button')?.focus();}
    if (event.key === 'Escape') hideSuggestions();
  });
  $('#buddy-suggestions').addEventListener('keydown', event => {
    const buttons = [...$('#buddy-suggestions').querySelectorAll('button')], index = buttons.indexOf(document.activeElement);
    if (event.key === 'ArrowDown') {event.preventDefault(); buttons[(index + 1) % buttons.length]?.focus();}
    if (event.key === 'ArrowUp') {event.preventDefault(); if (index === 0) $('#buddy-author').focus(); else buttons[index - 1]?.focus();}
    if (event.key === 'Escape') {$('#buddy-author').focus(); hideSuggestions();}
  });
  $('#buddy-suggestions').addEventListener('click', event => {
    const button = event.target.closest('[data-buddy-author]'); if (button) findBuddy(button.dataset.buddyAuthor);
  });
  for (const id of ['buddy-minimum', 'buddy-epsilon', 'buddy-exclude']) $('#' + id).addEventListener('change', () => {if (states.buddy.author) findBuddy();});
  $('#buddy-cancel').addEventListener('click', () => {stopBuddy(); states.buddy.progress = 'Matching cancelled.'; updateContext(states.buddy);});
  $('#buddy-method').addEventListener('click', () => {$('#about').showModal(); $('#buddy-explanation').scrollIntoView({block: 'start'});});
  $('#search-form').addEventListener('submit', event => {event.preventDefault(); view.mode === 'compare' ? runComparison() : search('semantic', $('#search').value);});
  $$('[data-mode]').forEach(button => button.addEventListener('click', () => switchMode(button.dataset.mode)));
  for (const field of ['search', 'search-b']) {
    const input = $('#' + field), suggestions = $('#' + input.getAttribute('aria-controls'));
    input.addEventListener('input', () => {state[field === 'search' ? 'draft' : 'draftB'] = input.value; state[field === 'search' ? 'choiceA' : 'choiceB'] = null; renderQueryTypes(); showSuggestions(field);});
    input.addEventListener('focus', () => {activeCompareField = field; showSuggestions(field);});
    input.addEventListener('keydown', event => {
      if (event.key === 'Escape') hideSuggestions();
      if (event.key === 'ArrowDown' && !suggestions.hidden) {event.preventDefault(); suggestions.querySelector('button')?.focus();}
    });
    suggestions.addEventListener('keydown', event => {
      const buttons = [...suggestions.querySelectorAll('button')], index = buttons.indexOf(document.activeElement);
      if (event.key === 'ArrowDown') {event.preventDefault(); buttons[(index + 1) % buttons.length]?.focus();}
      if (event.key === 'ArrowUp') {event.preventDefault(); if (index === 0) input.focus(); else buttons[index - 1]?.focus();}
      if (event.key === 'Escape') {hideSuggestions(); input.focus();}
    });
    suggestions.addEventListener('click', event => {
      const button = event.target.closest('[data-search-kind]'); if (!button) return;
      if (view.mode !== 'compare') {search(button.dataset.searchKind, button.dataset.value); return;}
      setCompareChoice(field, criterion(button.dataset.searchKind, button.dataset.value));
      hideSuggestions(); if ($('#search').value.trim() && $('#search-b').value.trim()) runComparison();
      else (field === 'search' ? $('#search-b') : $('#search')).focus();
    });
  }
  $$('[data-example]').forEach(button => button.addEventListener('click', () => {
    const field = view.mode === 'compare' ? activeCompareField : 'search';
    $('#' + field).value = button.dataset.example; state[field === 'search' ? 'draft' : 'draftB'] = button.dataset.example;
    state[field === 'search' ? 'choiceA' : 'choiceB'] = null; renderQueryTypes();
    $('#' + field).focus(); showSuggestions(field);
  }));
  $('#clear-search').addEventListener('click', clearSearch);
  $('#show-results').addEventListener('click', () => {
    if (view.mode !== 'neighbors') {$('#neighbor-list [data-result]')?.focus(); return;}
    $('#search-results').hidden = !$('#search-results').hidden;
    $('#show-results').setAttribute('aria-expanded', String(!$('#search-results').hidden));
    const rect = $('#search-state').getBoundingClientRect(); $('#search-results').style.top = `${rect.bottom + 5 + scrollY}px`;
  });
  $('#search-results').addEventListener('click', event => {const button = event.target.closest('[data-result]'); if (button) {select(Number(button.dataset.result)); $('#search-results').hidden = true; $('#show-results').setAttribute('aria-expanded', 'false');}});
  document.addEventListener('pointerdown', event => {
    if (!event.target.closest('.search-wrap')) hideSuggestions();
    if (!$('#search-results').contains(event.target) && !$('#show-results').contains(event.target)) {$('#search-results').hidden = true; $('#show-results').setAttribute('aria-expanded', 'false');}
  });
  $('#dismiss-error').addEventListener('click', () => {state.error = ''; $('#error').hidden = true;});
  $('#neighbor-list').addEventListener('click', event => {
    const buddy = event.target.closest('[data-buddy]'); if (buddy) {selectBuddy(buddy.dataset.buddy); return;}
    const neighbor = event.target.closest('[data-neighbor]'), result = event.target.closest('[data-result]');
    if (neighbor) compare(Number(neighbor.dataset.neighbor)); else if (result) select(Number(result.dataset.result));
  });
  $$('[data-set-filter]').forEach(button => button.addEventListener('click', () => {
    state.filter = button.dataset.setFilter; renderNeighbors(); $('#neighbor-list').scrollTop = 0;
  }));

  $('#paper-scroll').addEventListener('click', event => {
    const buddyPaper = event.target.closest('[data-buddy-paper]');
    if (buddyPaper) {select(Number(buddyPaper.dataset.buddyPaper)); $('#paper-scroll .paper-entry')?.scrollIntoView({block: 'nearest'}); return;}
    const expand = event.target.closest('[data-expand-paper]');
    if (expand) {
      const index = Number(expand.dataset.expandPaper), top = $('#paper-scroll').scrollTop;
      state.expandedPapers.has(index) ? state.expandedPapers.delete(index) : state.expandedPapers.add(index);
      renderPapers(); $('#paper-scroll').scrollTop = top;
      $(`[data-expand-paper="${index}"]`)?.focus({preventScroll: true});
    }
    if (event.target.closest('[data-make-anchor]')) select(state.comparison);
  });
  $('#end-compare').addEventListener('click', () => {state.comparison = null; state.expandedPapers.clear(); renderPapers(); renderNeighbors(); renderLegend(); $('#paper-scroll').scrollTop = 0; scheduleDraw();});
  $$('[data-tab]').forEach(button => button.addEventListener('click', () => {
    state.tab = button.dataset.tab; $$('[data-tab]').forEach(b => b.setAttribute('aria-pressed', String(b === button))); renderPapers(); $('#paper-scroll').scrollTop = 0;
  }));
  $$('[data-projection]').forEach(button => button.addEventListener('click', () => {
    view.projection = button.dataset.projection; $$('[data-projection]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    renderPapers(); scheduleDraw(); persist();
  }));
  $('#color-by').value = view.color;
  $('#color-by').addEventListener('change', event => {view.color = event.target.value; renderLegend(); scheduleDraw(); persist();});
  $('#reset-view').addEventListener('click', () => {view.rotation = -.45; view.tilt = .27; view.zoom = 1; scheduleDraw();});
  $('#expand-plot').addEventListener('click', () => {
    view.expanded = !view.expanded; $('#workspace').classList.toggle('expanded', view.expanded);
    $('#expand-plot').setAttribute('aria-label', view.expanded ? 'Restore paper columns' : 'Expand plot');
    $('#expand-plot').setAttribute('aria-pressed', String(view.expanded));
  });

  function closestPoint(event) {
    const rect = canvas.getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top;
    let nearest = null, best = event.pointerType === 'touch' ? 24 ** 2 : 11 ** 2;
    for (const item of projected) {
      const distance = (item.p[0] - x) ** 2 + (item.p[1] - y) ** 2;
      if (distance < best) {best = distance; nearest = item;}
    }
    return nearest;
  }
  canvas.addEventListener('pointerdown', event => {
    drag = [event.clientX, event.clientY, view.rotation, view.tilt]; moved = false;
    canvas.setPointerCapture(event.pointerId); $('#plot-tooltip').hidden = true;
  });
  canvas.addEventListener('pointermove', event => {
    if (drag) {
      const dx = event.clientX - drag[0], dy = event.clientY - drag[1];
      if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
      if (moved) {view.rotation = drag[2] + dx * .008; view.tilt = Math.max(-1.25, Math.min(1.25, drag[3] + dy * .008)); scheduleDraw();}
      return;
    }
    const item = closestPoint(event), tip = $('#plot-tooltip');
    if (!item) {tip.hidden = true; return;}
    const paper = papers[item.i];
    tip.innerHTML = `${escape(paper.title)}<small>${escape(citation(paper))}</small>`;
    tip.hidden = false;
    tip.style.left = `${Math.max(5, Math.min(width - tip.offsetWidth - 8, item.p[0] + 13))}px`;
    tip.style.top = `${Math.max(60, Math.min(height - tip.offsetHeight - 30, item.p[1] - 20))}px`;
  });
  canvas.addEventListener('pointerup', event => {if (!moved) {const item = closestPoint(event); if (item) select(item.i);} drag = null;});
  canvas.addEventListener('pointercancel', () => {drag = null;});
  canvas.addEventListener('pointerleave', () => {$('#plot-tooltip').hidden = true;});
  canvas.addEventListener('wheel', event => {event.preventDefault(); view.zoom = Math.max(.45, Math.min(4, view.zoom * (event.deltaY > 0 ? .93 : 1.07))); scheduleDraw();}, {passive: false});
  canvas.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft') view.rotation -= .12;
    else if (event.key === 'ArrowRight') view.rotation += .12;
    else if (event.key === 'ArrowUp') view.tilt = Math.max(-1.25, view.tilt - .1);
    else if (event.key === 'ArrowDown') view.tilt = Math.min(1.25, view.tilt + .1);
    else if (event.key === '+' || event.key === '=') view.zoom = Math.min(4, view.zoom * 1.1);
    else if (event.key === '-') view.zoom = Math.max(.45, view.zoom / 1.1);
    else if (event.key.toLowerCase() === 'n') select(((state.selected ?? -1) + 1) % papers.length);
    else return;
    event.preventDefault(); scheduleDraw();
  });

  $('#help-button').addEventListener('click', () => $('#about').showModal());
  $('#demo-queries').addEventListener('click', () => {$('#about').showModal(); $('#demo-topic-list').scrollIntoView({block: 'nearest'});});
  $('#demo-topic-list').innerHTML = data.queries.map(q => `<button type="button" data-demo-query="${escape(q.text)}">${escape(q.text)}</button>`).join('');
  $('#demo-topic-list').addEventListener('click', event => {
    const button = event.target.closest('[data-demo-query]'); if (!button) return;
    $('#about').close();
    if (view.mode !== 'compare') {search('semantic', button.dataset.demoQuery); return;}
    const field = activeCompareField; $('#' + field).value = button.dataset.demoQuery;
    state[field === 'search' ? 'draft' : 'draftB'] = button.dataset.demoQuery;
    state[field === 'search' ? 'choiceA' : 'choiceB'] = null; renderQueryTypes();
    if ($('#search').value.trim() && $('#search-b').value.trim()) runComparison();
    else (field === 'search' ? $('#search-b') : $('#search')).focus();
  });
  $('#about').addEventListener('click', event => {if (event.target === $('#about')) {const rect = $('#about').getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $('#about').close();}});
  $('#dataset-note').textContent = `${papers.length.toLocaleString()} confirmed publications. Projections prepared ${data.createdAt?.slice(0, 10) || 'locally'}. Current author names and branch annotations come from the publication metadata; this explorer does not infer missing affiliations.`;
  try {
    const stored = JSON.parse(localStorage.getItem(storageKey) || '[]');
    if (!Array.isArray(stored)) throw new Error('Invalid saved-paper collection.');
    for (const record of stored) {
      try {const index = installPaper(record, record.vector); rememberLookup(index);}
      catch {states.add.storageWarning = 'Some saved papers could not be restored. Add their PMIDs again.';}
    }
    if (addedRecords.length) states.add.notice = `${addedRecords.length} added papers restored from this browser.`;
  } catch {states.add.storageWarning = 'Browser storage is unavailable; new papers can still be added for this session.';}
  if (byId.has(saved.paper)) states.neighbors.selected = byId.get(saved.paper);
  updateCollectionLabels();
  $$('[data-projection]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.projection === view.projection)));
  renderMode(); new ResizeObserver(resize).observe($('#plot')); resize(); $('#loading').hidden = true;
})();
