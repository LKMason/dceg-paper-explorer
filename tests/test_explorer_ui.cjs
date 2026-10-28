const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, 'explorer', name), 'utf8');

function fixture() {
  const vectors = [], papers = [], coords = [];
  for (let i = 0; i < 120; i++) {
    const angle = i * 2 * Math.PI / 120;
    vectors.push(Math.cos(angle), Math.sin(angle)); coords.push([Math.cos(angle), Math.sin(angle), i / 120]);
    papers.push({id: 'paper:' + i, title: 'Paper ' + i, abstract: 'An abstract about cancer research. '.repeat(30),
      authors: ['Researcher ' + (i % 3), 'Oral Cancer GWAS'], firstAuthor: 'Researcher ' + (i % 3),
      date: '2026-01-01', year: '2026', journal: 'Test journal', branches: i % 2 ? ['REB'] : [],
      types: 'Journal Article', tags: '', doi: '', pmid: i === 0 ? '10000000' : '', url: '',
      neighbors: Array.from({length: 10}, (_, j) => [(i + j + 1) % 120, .1 + j / 100])});
  }
  return {papers, branches: [{code: 'REB', name: 'Radiation Epidemiology Branch'}],
    authorTypes: {'Oral Cancer GWAS': 'group', 'Researcher 0': 'person', 'Researcher 1': 'person', 'Researcher 2': 'person'},
    coordinates: {umap: coords, tsne: coords, pca: coords}, dimensions: 2,
    pcaTransform: {mean: [0, 0], components: [[1, 0], [0, 1], [0, 0]]},
    vectors: Buffer.from(new Float32Array(vectors).buffer).toString('base64'),
    queries: [{text: 'polygenic risk', vector: [1, 0]}, {text: 'breast cancer', vector: [Math.cos(.5), Math.sin(.5)]}, {text: 'air pollution', vector: [-1, 0]}],
    browserModel: {}, createdAt: '2026-10-23'};
}

async function setup(options = {}) {
  const setupOptions = options;
  const payload = options.payload || fixture();
  const template = read('index.template.html').replace(/<!--__TEMPLATE_REDIRECT_START__-->[\s\S]*?<!--__TEMPLATE_REDIRECT_END__-->/, '');
  const html = template.replace('/*__EXPLORER_DATA__*/', JSON.stringify(payload)).replace('/*__EXPLORER_STYLES__*/', read('styles.css'));
  const dom = new JSDOM(html, {runScripts: 'outside-only', pretendToBeVisual: true, url: 'https://explorer.test/'});
  const window = dom.window, document = window.document, workerSource = read('search-worker.js');
  const workers = [];
  if (options.fetch) window.fetch = options.fetch;
  for (const [key, value] of Object.entries(options.stored || {})) window.localStorage.setItem(key, value);
  if (options.storageFull) window.Storage.prototype.setItem = () => {throw new Error('Storage quota exceeded');};
  const context = {fills: [], clearRect() {this.fills = [];}, fill() {this.fills.push(this.fillStyle);}, setTransform() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, arc() {}, fillText() {}, setLineDash() {}};
  window.HTMLCanvasElement.prototype.getContext = () => context;
  document.querySelector('#plot').getBoundingClientRect = () => ({width: 880, height: 540});
  window.ResizeObserver = class {constructor(fn) {this.fn = fn;} observe() {this.fn();}};
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.HTMLDialogElement.prototype.showModal = function () {this.open = true;};
  window.HTMLDialogElement.prototype.close = function () {this.open = false;};
  window.URL.createObjectURL = () => 'blob:test-worker'; window.URL.revokeObjectURL = () => {};
  window.Worker = class {
    constructor(url, options) {
      workers.push(this); this.messages = []; this.hold = false; this.waiting = [];
      this.scope = {self: {}, atob, console, setTimeout, Float32Array,
        postMessage: message => {this.messages.push(message); if (this.hold) this.waiting.push(message); else setTimeout(() => {if (!this.terminated) this.onmessage?.({data: message});}, 0);}};
      vm.createContext(this.scope);
      this.isBuddy = options?.name === 'paper-buddy';
      vm.runInContext(this.isBuddy ? read('buddy.js') + '\n' + read('buddy-worker.js') : workerSource, this.scope);
      if (setupOptions.encoder && !this.isBuddy) {
        this.scope.encoder = setupOptions.encoder; this.scope.encodedTexts = [];
        vm.runInContext(`loadEncoder = async () => ({tokenizer: async (text, options) => {
          encodedTexts.push({text, options}); return {input_ids: {dims: [1, encoder.tokens || 10]}};
        }, model: async () => {
          if (encoder.error) throw new Error(encoder.error);
          return {sentence_embedding: {data: encoder.vector}};
        }});`, this.scope);
      }
    }
    postMessage(message) {this.scope.self.onmessage({data: message});}
    release() {this.hold = false; for (const message of this.waiting.splice(0)) this.onmessage?.({data: message});}
    terminate() {this.terminated = true;}
  };
  for (const name of ['autocomplete.js', 'modes.js', 'pubmed.js', 'paper-space.js', 'app.js']) window.eval(read(name));
  const $ = selector => document.querySelector(selector);
  const input = (selector, value) => {$(selector).value = value; $(selector).dispatchEvent(new window.Event('input', {bubbles: true}));};
  const submit = () => $('#search-form').dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
  const tick = () => new Promise(resolve => setTimeout(resolve, 45));
  const mode = name => $(`[data-mode="${name}"]`).click();
  await tick();
  return {dom, window, document, $, input, submit, tick, mode, workers, context};
}

test('mode interactions with the real offline ranking worker', async t => {
  const ui = await setup();
  const {$, input, submit, tick, mode, workers, document} = ui;
  t.after(() => ui.dom.window.close());
  await t.test('Search uses the list for results and opens only one paper', async () => {
    assert.equal($('#list-heading').textContent, 'Search results');
    assert.equal($('#seed-ref').hidden, true);
    input('#search', 'polygenic risk'); submit(); await tick();
    assert.equal(document.querySelectorAll('#neighbor-list [data-result]').length, 50);
    assert.equal(document.querySelectorAll('[data-neighbor]').length, 0);
    $('#neighbor-list [data-result]').click();
    assert.equal(document.querySelectorAll('.paper-entry').length, 1);
    assert.equal($('#compare-bar').hidden, true);
    assert.equal($('#color-by').hidden, true);
  });
  await t.test('Neighbors overrides previous colors with its selected neighborhood', async () => {
    mode('neighbors'); await tick();
    assert.equal($('#list-heading').textContent, 'Nearest papers');
    assert.equal(document.querySelectorAll('[data-neighbor]').length, 10);
    assert.equal($('#color-by').hidden, true);
    assert.equal($('#mode-color').textContent, 'Nearest neighbors');
    assert.equal(ui.context.fills.filter(color => color === '#111111').length, 1);
    assert.equal(ui.context.fills.filter(color => color === '#447b9a').length, 10);
    assert.equal(ui.context.fills.filter(color => color === '#a1a69a').length, 109);
    const anchor = document.querySelector('.paper-entry h2').textContent;
    $('[data-neighbor]').click(); await tick();
    assert.equal(document.querySelectorAll('.paper-entry').length, 2);
    assert.equal(document.querySelector('.paper-entry h2').textContent, anchor);
    assert.equal(ui.context.fills.filter(color => color === '#447b9a').length, 9);
    assert.equal(ui.context.fills.filter(color => color === '#c7684c').length, 1);
    $('#color-by').value = 'author';
    $('#color-by').dispatchEvent(new ui.window.Event('change')); await tick();
    assert.equal(ui.context.fills.filter(color => color === '#111111').length, 1);
    assert.equal(ui.context.fills.filter(color => color === '#447b9a').length, 9);
    assert.equal(ui.context.fills.filter(color => color === '#c7684c').length, 1);
  });
  await t.test('returning to Search restores its results and query', async () => {
    mode('search');
    assert.equal($('#search').value, 'polygenic risk');
    assert.equal(document.querySelectorAll('#neighbor-list [data-result]').length, 50);
    assert.equal(document.querySelectorAll('.paper-entry').length, 1);
  });
  await t.test('Compare combines exactly two top-50 sets and colors overlap black', async () => {
    mode('compare'); assert.equal($('#query-b-wrap').hidden, false);
    input('#search', 'polygenic risk'); input('#search-b', 'breast cancer'); submit(); await tick();
    const counts = Object.fromEntries([...document.querySelectorAll('[data-set-count]')].map(e => [e.dataset.setCount, Number(e.textContent)]));
    assert.equal(counts.a + counts.both, 50); assert.equal(counts.b + counts.both, 50);
    assert.ok(counts.both > 0); assert.equal(counts.all, counts.a + counts.b + counts.both);
    assert.equal(ui.context.fills.filter(color => color === '#111111').length, counts.both);
    assert.equal(document.querySelectorAll('.paper-entry').length, 1);
    $('[data-set-filter="both"]').click();
    assert.equal(document.querySelectorAll('#neighbor-list [data-result]').length, counts.both);
    $('#neighbor-list [data-result]').click();
    assert.equal(document.querySelectorAll('.paper-entry').length, 1);
    assert.ok($('.query-scores').textContent.includes('BOTH QUERIES'));
  });
  await t.test('Compare rejects an empty second query', async () => {
    input('#search-b', ''); submit();
    assert.equal($('#error').hidden, false); assert.match($('#error').textContent, /both query A and query B/);
  });
  await t.test('late Compare results stay in Compare after switching to Search', async () => {
    input('#search-b', 'air pollution'); workers[0].hold = true; submit(); await tick();
    mode('search'); const title = $('.paper-entry h2').textContent;
    workers[0].release(); await tick();
    assert.equal($('#app').dataset.mode, 'search');
    assert.equal($('.paper-entry h2').textContent, title);
    assert.equal($('#search').value, 'polygenic risk');
    mode('compare');
    assert.equal($('#search-b').value, 'air pollution');
    assert.equal(Number($('[data-set-count="both"]').textContent), 0);
    assert.equal(document.querySelectorAll('#neighbor-list [data-result]').length, 100);
  });
  await t.test('cleared requests cannot repopulate a result list', async () => {
    workers[0].hold = true; input('#search-b', 'breast cancer'); submit(); await tick();
    $('#clear-search').click(); workers[0].release(); await tick();
    assert.equal($('#search').value, ''); assert.equal($('#search-b').value, '');
    assert.equal(document.querySelectorAll('#neighbor-list [data-result]').length, 0);
    assert.equal(document.querySelectorAll('.paper-entry').length, 0);
  });
  await t.test('Neighbors retains its selected pair across other modes', async () => {
    mode('neighbors'); await tick();
    assert.equal(document.querySelectorAll('.paper-entry').length, 2);
    assert.equal($('#compare-bar').hidden, false);
    assert.equal(ui.context.fills.filter(color => color === '#111111').length, 1);
    assert.equal(ui.context.fills.filter(color => color === '#447b9a').length, 9);
    assert.equal(ui.context.fills.filter(color => color === '#c7684c').length, 1);
    mode('search');
    assert.equal(document.querySelectorAll('.paper-entry').length, 1);
  });
  await t.test('group autocomplete is available in both Search and Compare', async () => {
    mode('search'); input('#search', 'GWAS');
    assert.ok($('#suggestions [data-search-kind="group"]'));
    $('#suggestions [data-search-kind="group"]').click();
    assert.match($('#search-label').textContent, /Group author/);
    assert.equal(document.querySelectorAll('#neighbor-list [data-result]').length, 120);
    mode('compare'); input('#search', 'GWAS');
    assert.ok($('#suggestions [data-search-kind="group"]'));
  });
  await t.test('a failed comparison query never reports a completed empty overlap', async () => {
    input('#search', 'an uncached custom query'); input('#search-b', 'breast cancer'); submit(); await tick();
    assert.equal($('#error').hidden, false); assert.match($('#error').textContent, /Query A/);
    assert.equal($('#set-filters').hidden, true);
    assert.equal(document.querySelectorAll('#neighbor-list [data-result]').length, 0);
    assert.equal($('#show-results').hidden, true);
    input('#search', 'polygenic risk'); submit(); await tick();
    assert.equal($('#error').hidden, true);
    assert.ok(Number($('[data-set-count="both"]').textContent) > 0);
  });
  await t.test('Compare supports exact group and branch sets without truncation or model requests', async () => {
    $('#clear-search').click();
    const messageCount = workers[0].messages.length;
    input('#search', 'GWAS'); $('#suggestions [data-search-kind="group"]').click();
    assert.equal($('#query-type-a').textContent, 'Group');
    input('#search-b', 'Radiation'); $('#suggestions-b [data-search-kind="branch"]').click(); await tick();
    assert.equal($('#query-type-b').textContent, 'Branch');
    assert.equal($('#search-b').value, 'Radiation Epidemiology Branch');
    assert.equal(Number($('[data-set-count="all"]').textContent), 120);
    assert.equal(Number($('[data-set-count="both"]').textContent), 60);
    assert.equal(Number($('[data-set-count="a"]').textContent), 60);
    assert.equal(Number($('[data-set-count="b"]').textContent), 0);
    assert.equal(workers[0].messages.length, messageCount);
    assert.match($('.query-scores').textContent, /A: exact match/);
    assert.match($('.query-scores').textContent, /B: exact match/);
    $('[data-set-filter="a"]').click(); $('#neighbor-list [data-result]').click();
    assert.match($('.query-scores').textContent, /B: not matched/);
    assert.match($('#search-progress').textContent, /A: 120 exact matches · B: 60 exact matches/);
    mode('neighbors'); mode('compare');
    assert.equal($('#query-type-a').textContent, 'Group');
    assert.equal($('#query-type-b').textContent, 'Branch');
    assert.equal($('#search-b').value, 'Radiation Epidemiology Branch');
    assert.equal(Number($('[data-set-count="all"]').textContent), 120);
  });
  await t.test('Compare mixes person filters with semantic ranking on either side', async () => {
    $('#clear-search').click();
    input('#search-b', 'Researcher 0'); $('#suggestions-b [data-search-kind="researcher"]').click();
    assert.equal($('#query-type-b').textContent, 'Person');
    input('#search', 'polygenic risk'); submit(); await tick();
    const a = Number($('[data-set-count="a"]').textContent), b = Number($('[data-set-count="b"]').textContent);
    const both = Number($('[data-set-count="both"]').textContent);
    assert.equal(a + both, 50); assert.equal(b + both, 40);
    assert.match($('.query-scores').textContent, /A: \d\.\d{3}/);
    assert.match($('.query-scores').textContent, /B: exact match/);
    input('#search-b', 'air pollution');
    assert.equal($('#query-type-b').textContent, 'Meaning');
    // Draft edits must not relabel the already displayed exact-match results.
    $('#neighbor-list [data-result]').click();
    assert.match($('.query-scores').textContent, /B: exact match/);
    submit(); await tick();
    assert.equal(Number($('[data-set-count="a"]').textContent), 50);
    assert.equal(Number($('[data-set-count="b"]').textContent), 50);
    assert.equal(Number($('[data-set-count="both"]').textContent), 0);
    assert.match($('.query-scores').textContent, /B: outside top 50/);
  });
});

function importedXML(pmid, abstract = 'A complete abstract for an external paper. '.repeat(40)) {
  return `<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>${pmid}</PMID><Article>
    <ArticleTitle>External paper ${pmid}</ArticleTitle><Journal><Title>External Journal</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>
    <AuthorList><Author><ForeName>External</ForeName><LastName>Scientist</LastName></Author></AuthorList>
    <Abstract><AbstractText>${abstract}</AbstractText></Abstract><PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
    </Article></MedlineCitation></PubmedArticle></PubmedArticleSet>`;
}

test('PMID addition joins the shared client-side space and restores after refresh', async t => {
  let fetchCount = 0;
  const encoder = {vector: [2, 0]};
  const ui = await setup({encoder, fetch: async url => {fetchCount++; return {ok: true, text: async () => importedXML(new URL(url).searchParams.get('id'))};}});
  t.after(() => ui.dom.window.close());
  const {$, input, mode, tick, submit, workers, document} = ui;
  const add = async pmid => {input('#pmid', pmid); $('#pmid-form').dispatchEvent(new ui.window.Event('submit', {bubbles: true, cancelable: true})); await tick();};
  await t.test('existing paper is highlighted without a network or model request', async () => {
    mode('add'); await add('10000000');
    assert.equal($('#search-form').hidden, true); assert.equal($('#pmid-form').hidden, false);
    assert.match($('#pmid-message').textContent, /already in the space/);
    assert.equal($('.paper-entry h2').textContent, 'Paper 0');
    assert.equal(ui.context.fills.filter(c => c === '#111111').length, 1);
    assert.equal(fetchCount, 0); assert.equal(workers.length, 0);
  });
  await t.test('new non-DCEG paper uses full document text and a normalized document embedding', async () => {
    await add('12345');
    assert.equal(fetchCount, 1); assert.equal($('#error').hidden, true);
    assert.match($('#pmid-message').textContent, /12345 added to the space/);
    assert.equal($('.paper-entry h2').textContent, 'External paper 12345');
    assert.match($('.new-paper-note').textContent, /estimated UMAP position/);
    assert.match($('#data-status').textContent, /120 base papers \+ 1 added/);
    const calls = workers[0].scope.encodedTexts;
    assert.equal(calls.length, 1); assert.equal(calls[0].options.truncation, false);
    assert.equal(calls[0].text, 'title: External paper 12345 | text: ' + 'A complete abstract for an external paper. '.repeat(40).trim());
    const message = workers[0].messages.find(m => m.type === 'paper-vector');
    assert.deepEqual(Array.from(message.vector), [1, 0]);
    $('[data-projection="pca"]').click();
    assert.match($('.new-paper-note').textContent, /fitted PCA position/);
    $('#pmid-neighbors').click();
    assert.equal($('#app').dataset.mode, 'neighbors');
    assert.equal($('.paper-entry h2').textContent, 'External paper 12345');
    assert.equal(document.querySelectorAll('[data-neighbor]').length, 10);
    assert.equal(Number($('[data-neighbor]').dataset.neighbor), 0);
    assert.equal($('[data-neighbor] .distance').textContent, '0.000');
  });
  await t.test('new paper participates in semantic ranking, author filters, and mixed comparisons', async () => {
    mode('search'); input('#search', 'polygenic risk'); submit(); await tick();
    assert.ok($('#neighbor-list [data-result="120"]'));
    input('#search', 'External'); $('#suggestions [data-search-kind="researcher"]').click();
    assert.equal(document.querySelectorAll('#neighbor-list [data-result]').length, 1);
    mode('compare'); input('#search', 'External'); $('#suggestions [data-search-kind="researcher"]').click();
    input('#search-b', 'polygenic risk'); submit(); await tick();
    assert.equal(Number($('[data-set-count="both"]').textContent), 1);
    assert.equal(Number($('[data-set-count="b"]').textContent), 49);
    assert.equal($('.paper-entry h2').textContent, 'External paper 12345');
    mode('add'); await add('12345');
    assert.match($('#pmid-message').textContent, /already in the space/);
    assert.equal(fetchCount, 1); assert.equal(workers[0].scope.encodedTexts.length, 1);
  });
  await t.test('local additions restore with their vectors and work without another PubMed lookup', async () => {
    const stored = Object.fromEntries(Array.from({length: ui.window.localStorage.length}, (_, i) => {
      const key = ui.window.localStorage.key(i); return [key, ui.window.localStorage.getItem(key)];
    }));
    const restored = await setup({stored, fetch: async () => {throw new Error('must not fetch');}});
    try {
      restored.mode('add'); assert.match(restored.$('#pmid-message').textContent, /restored/);
      assert.equal(restored.$('.paper-entry h2').textContent, 'External paper 12345');
      restored.mode('search'); restored.input('#search', 'polygenic risk'); restored.submit(); await restored.tick();
      assert.ok(restored.$('#neighbor-list [data-result="120"]'));
      restored.mode('neighbors');
      assert.equal(restored.$('.paper-entry h2').textContent, 'External paper 12345');
    } finally {restored.dom.window.close();}
  });
});

test('PMID failures leave the collection unchanged and permit retry', async t => {
  let response = '<PubmedArticleSet/>', offline = false;
  const encoder = {vector: [1, 0], error: 'model unavailable'};
  const ui = await setup({encoder, fetch: async () => {if (offline) throw new ui.window.TypeError('network unavailable'); return {ok: true, text: async () => response};}});
  t.after(() => ui.dom.window.close());
  const {$, input, tick} = ui; ui.mode('add');
  const add = async pmid => {input('#pmid', pmid); $('#pmid-form').dispatchEvent(new ui.window.Event('submit', {cancelable: true})); await tick();};
  await add('invalid'); assert.match($('#error').textContent, /valid numeric/);
  await add('12345'); assert.match($('#error').textContent, /No PubMed paper/);
  offline = true; await add('12345'); assert.match($('#error').textContent, /internet connection/); offline = false;
  response = importedXML('12345'); await add('12345'); assert.match($('#error').textContent, /model unavailable/);
  assert.equal($('#pmid-submit').disabled, false); assert.match($('#data-status').textContent, /120 base papers ·/);
  encoder.error = ''; encoder.tokens = 9000;
  await add('12345'); assert.match($('#error').textContent, /not been truncated or added/);
  encoder.tokens = 10; response = importedXML('12345', ''); await add('12345');
  assert.equal($('#error').hidden, true); assert.match($('#pmid-message').textContent, /title only/);
  assert.match($('#data-status').textContent, /\+ 1 added/);
});

test('background addition refreshes the visible landscape and existing query sets', async t => {
  let completeLookup;
  const ui = await setup({encoder: {vector: [1, 0]}, fetch: () => new Promise(resolve => {completeLookup = resolve;})});
  t.after(() => ui.dom.window.close());
  ui.input('#search', 'polygenic risk'); ui.submit(); await ui.tick();
  ui.mode('add'); ui.input('#pmid', '12345');
  ui.$('#pmid-form').dispatchEvent(new ui.window.Event('submit', {cancelable: true}));
  assert.equal(ui.$('#pmid-submit').disabled, true);
  ui.mode('search'); completeLookup({ok: true, text: async () => importedXML('12345')}); await ui.tick();
  assert.equal(ui.$('#app').dataset.mode, 'search');
  assert.ok(ui.$('#neighbor-list [data-result="120"]'));
  assert.equal(ui.$('.paper-entry h2').textContent, 'Paper 0');
  assert.equal(ui.context.fills.length, 121);
});

test('storage failure clearly marks a successful addition as session-only', async t => {
  const ui = await setup({storageFull: true, encoder: {vector: [1, 0]}, fetch: async () => ({ok: true, text: async () => importedXML('12345')})});
  t.after(() => ui.dom.window.close());
  ui.mode('add'); ui.input('#pmid', '12345'); ui.$('#pmid-form').dispatchEvent(new ui.window.Event('submit', {cancelable: true})); await ui.tick();
  assert.match(ui.$('#pmid-message').textContent, /Added for this session/);
  assert.match(ui.$('#data-status').textContent, /\+ 1 added/);
});

test('Paper buddy ranks real distributions in a separate worker and preserves mode state', async t => {
  const ui = await setup(); t.after(() => ui.dom.window.close());
  const {$, input, mode, tick, document, workers} = ui;
  await t.test('choosing an author ranks peers and excludes collective authors', async () => {
    mode('buddy'); assert.equal($('#buddy-form').hidden, false); assert.equal($('#search-form').hidden, true);
    input('#buddy-author', 'GWAS'); assert.equal($('#buddy-suggestions [data-buddy-author]'), null);
    input('#buddy-author', 'Researcher 0');
    $('#buddy-suggestions [data-buddy-author="Researcher 0"]').click(); await tick();
    assert.equal($('#error').hidden, true);
    const rows = [...document.querySelectorAll('[data-buddy]')];
    assert.equal(rows.length, 2); assert.ok(rows.every(row => row.dataset.buddy !== 'Researcher 0'));
    assert.match($('#buddy-status').textContent, /40 source papers · 2 candidates ranked/);
    assert.match($('.buddy-card').textContent, /Sinkhorn divergence/);
    assert.match($('#legend').textContent, /Shared papers/);
    assert.ok(workers.find(worker => worker.isBuddy));
    assert.equal(workers.some(worker => !worker.isBuddy), false);
    assert.equal(ui.context.fills.filter(color => color === '#447b9a').length, 40);
    assert.equal(ui.context.fills.filter(color => color === '#c7684c').length, 40);
  });
  await t.test('selecting another buddy changes the highlighted portfolio and exposes its papers', async () => {
    const rows = [...document.querySelectorAll('[data-buddy]')]; rows[1].click();
    assert.equal($('.buddy-card h2').textContent, rows[1].dataset.buddy);
    assert.equal(document.querySelectorAll('[data-buddy-paper]').length, 80);
    const button = $('[data-buddy-paper="0"]'); button.click();
    assert.equal($('.paper-entry h2').textContent, 'Paper 0');
    mode('search'); input('#search', 'polygenic risk'); ui.submit(); await tick();
    mode('buddy'); assert.equal($('#buddy-author').value, 'Researcher 0');
    assert.equal($('.paper-entry h2').textContent, 'Paper 0');
    assert.equal(document.querySelectorAll('[data-buddy]').length, 2);
  });
  await t.test('filtering and smoothing recompute the ranking without loading a model', async () => {
    $('#buddy-epsilon').value = '0.2'; $('#buddy-epsilon').dispatchEvent(new ui.window.Event('change')); await tick();
    assert.match($('.buddy-card').textContent, /ε = 0.2/);
    $('#buddy-exclude').checked = true; $('#buddy-exclude').dispatchEvent(new ui.window.Event('change')); await tick();
    assert.equal(document.querySelectorAll('[data-buddy]').length, 2);
  });
  await t.test('late results update only their originating mode', async () => {
    const buddyWorker = workers.find(worker => worker.isBuddy); buddyWorker.hold = true;
    $('#buddy-form').dispatchEvent(new ui.window.Event('submit', {cancelable: true})); await tick();
    mode('search'); const title = $('.paper-entry h2').textContent;
    buddyWorker.release(); await tick();
    assert.equal($('#app').dataset.mode, 'search'); assert.equal($('.paper-entry h2').textContent, title);
    mode('buddy'); assert.equal(document.querySelectorAll('[data-buddy]').length, 2);
  });
  await t.test('cancellation and invalid names cannot produce stale rankings', async () => {
    const worker = workers.find(worker => worker.isBuddy); worker.hold = true;
    $('#buddy-form').dispatchEvent(new ui.window.Event('submit', {cancelable: true})); await tick();
    $('#buddy-cancel').click(); worker.release(); await tick();
    assert.equal(document.querySelectorAll('[data-buddy]').length, 0);
    assert.match($('#buddy-status').textContent, /cancelled/);
    input('#buddy-author', 'Oral Cancer GWAS'); $('#buddy-form').dispatchEvent(new ui.window.Event('submit', {cancelable: true}));
    assert.match($('#error').textContent, /individual author/);
  });
});

test('buddy coauthor exclusion uses shared papers and handles an empty eligible set', async t => {
  const payload = fixture(); payload.papers[0].authors.push('Researcher 1');
  for (let i = 8; i < payload.papers.length; i += 3) {
    payload.papers[i].authors = ['Oral Cancer GWAS']; payload.papers[i].firstAuthor = 'Oral Cancer GWAS';
  }
  const ui = await setup({payload}); t.after(() => ui.dom.window.close());
  ui.mode('buddy'); ui.$('#buddy-minimum').value = '1'; ui.input('#buddy-author', 'Researcher 0'); ui.$('#buddy-suggestions [data-buddy-author]').click(); await ui.tick();
  assert.equal(ui.document.querySelectorAll('[data-buddy]').length, 2);
  ui.$('#buddy-exclude').checked = true; ui.$('#buddy-exclude').dispatchEvent(new ui.window.Event('change')); await ui.tick();
  assert.equal(ui.document.querySelectorAll('[data-buddy]').length, 1);
  assert.equal(ui.$('[data-buddy]').dataset.buddy, 'Researcher 2');
  assert.match(ui.$('#buddy-status').textContent, /1 coauthors excluded/);
  ui.$('#buddy-minimum').value = '3';
  ui.$('#buddy-minimum').dispatchEvent(new ui.window.Event('change')); await ui.tick();
  assert.equal(ui.document.querySelectorAll('[data-buddy]').length, 0);
  assert.match(ui.$('#neighbor-list').textContent, /No buddies match/);
  assert.equal(ui.$('#error').hidden, true);
});

test('added papers invalidate buddy caches and can introduce new candidate authors', async t => {
  const ui = await setup({encoder: {vector: [1, 0]}, fetch: async () => ({ok: true, text: async () => importedXML('12345')})});
  t.after(() => ui.dom.window.close());
  ui.mode('buddy'); ui.$('#buddy-minimum').value = '1';
  ui.input('#buddy-author', 'Researcher 0'); ui.$('#buddy-suggestions [data-buddy-author]').click(); await ui.tick();
  assert.equal(ui.document.querySelectorAll('[data-buddy]').length, 2);
  ui.mode('add'); ui.input('#pmid', '12345'); ui.$('#pmid-form').dispatchEvent(new ui.window.Event('submit', {cancelable: true})); await ui.tick();
  ui.mode('buddy'); await ui.tick();
  assert.equal(ui.document.querySelectorAll('[data-buddy]').length, 3);
  assert.ok(ui.$('[data-buddy="External Scientist"]'));
  ui.input('#buddy-author', 'External Scientist'); ui.$('#buddy-suggestions [data-buddy-author]').click(); await ui.tick();
  assert.match(ui.$('.buddy-card').textContent, /Sparse portfolios/);
  assert.match(ui.$('#buddy-status').textContent, /1 source papers/);
});
