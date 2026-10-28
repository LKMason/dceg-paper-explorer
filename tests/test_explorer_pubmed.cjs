const assert = require('node:assert/strict');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');
const pubmed = require('../explorer/pubmed.js');
const space = require('../explorer/paper-space.js');
const Parser = new JSDOM('').window.DOMParser;
const xml = `<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345</PMID><Article>
  <ArticleTitle>A study of <i>risk</i> &amp; health</ArticleTitle>
  <Journal><Title>A Journal</Title><JournalIssue><PubDate><Year>2025</Year><Month>Feb</Month><Day>3</Day></PubDate></JournalIssue></Journal>
  <Abstract><AbstractText Label="BACKGROUND">First <b>section</b>.</AbstractText><AbstractText Label="RESULTS">Second section.</AbstractText></Abstract>
  <AuthorList><Author><ForeName>Jane A</ForeName><LastName>Smith</LastName></Author><Author><CollectiveName>Study Group</CollectiveName></Author></AuthorList>
  <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
  </Article><MeshHeadingList><MeshHeading><DescriptorName>Cancer</DescriptorName></MeshHeading></MeshHeadingList></MedlineCitation>
  <PubmedData><ArticleIdList><ArticleId IdType="doi">10.123/ABC</ArticleId></ArticleIdList></PubmedData>
  </PubmedArticle></PubmedArticleSet>`;

test('PMID input accepts IDs, prefixes, and PubMed links; rejects arbitrary URLs and invalid IDs', () => {
  for (const value of ['12345', '  PMID: 0012345 ', 'https://pubmed.ncbi.nlm.nih.gov/12345/']) assert.equal(pubmed.normalizePMID(value), '12345');
  for (const value of ['', '0', '-123', 'hello', '123,456', 'https://example.com/12345/', '123456789012345']) assert.throws(() => pubmed.normalizePMID(value), /valid numeric/);
});

test('PubMed parsing preserves structured abstracts and source-backed author types', () => {
  const {paper, authorTypes} = pubmed.parse(xml, '12345', Parser);
  assert.equal(paper.title, 'A study of risk & health');
  assert.equal(paper.abstract, 'BACKGROUND: First section.\n\nRESULTS: Second section.');
  assert.deepEqual(paper.authors, ['Jane A Smith', 'Study Group']);
  assert.deepEqual(authorTypes, {'Jane A Smith': 'person', 'Study Group': 'group'});
  assert.equal(paper.date, '2025-02-03'); assert.equal(paper.doi, '10.123/abc');
  assert.deepEqual(paper.branches, []); assert.equal(paper.tags, 'Cancer');
});

test('book records, missing abstracts, and partial dates remain valid papers', () => {
  const book = '<PubmedArticleSet><PubmedBookArticle><BookDocument><PMID>12</PMID><Book><BookTitle>Reference Book</BookTitle><PubDate><MedlineDate>2023 Winter</MedlineDate></PubDate></Book></BookDocument></PubmedBookArticle></PubmedArticleSet>';
  const {paper} = pubmed.parse(book, '12', Parser);
  assert.equal(paper.title, 'Reference Book'); assert.equal(paper.abstract, '');
  assert.equal(paper.date, '2023'); assert.equal(paper.firstAuthor, 'Unknown author');
});

test('missing, malformed, or mismatched PubMed records cannot be imported', () => {
  assert.throws(() => pubmed.parse('<PubmedArticleSet/>', '12345', Parser), /No PubMed paper/);
  assert.throws(() => pubmed.parse('<PubmedArticleSet>', '12345', Parser), /unreadable/);
  assert.throws(() => pubmed.parse(xml, '67890', Parser), /No PubMed paper/);
});

test('lookup uses the official API, handles rate limits, and does not accept an arbitrary endpoint', async () => {
  let requested;
  const record = await pubmed.lookup('12345', {Parser, fetch: async (url, options) => {
    requested = url; assert.equal(options.credentials, 'omit'); return {ok: true, text: async () => xml};
  }});
  assert.equal(new URL(requested).hostname, 'eutils.ncbi.nlm.nih.gov');
  assert.equal(new URL(requested).searchParams.get('id'), '12345');
  assert.equal(record.paper.pmid, '12345');
  await assert.rejects(pubmed.lookup('12345', {Parser, fetch: async () => ({ok: false, status: 429})}), /Wait a moment/);
});

test('new vectors use fitted PCA and estimated fixed-layout positions without moving base points', () => {
  const corpus = new Float32Array([1, 0, 0, 1, -1, 0]);
  const coordinates = {umap: [[10, 20, 30], [40, 50, 60], [70, 80, 90]], tsne: [[1, 2, 3], [4, 5, 6], [7, 8, 9]]};
  const pca = {mean: [.5, .25], components: [[1, 0], [0, 1], [.5, .5]]};
  const snapshot = JSON.stringify(coordinates);
  const plan = space.plan([2, 0], corpus, 2, coordinates, pca, 3);
  assert.deepEqual(Array.from(plan.vector), [1, 0]);
  assert.deepEqual(plan.positions.pca, [.5, -.25, .125]);
  assert.deepEqual(plan.positions.umap, coordinates.umap[0]);
  assert.deepEqual(plan.positions.tsne, coordinates.tsne[0]);
  assert.deepEqual(plan.neighbors, [[0, 0], [1, 1], [2, 2]]);
  assert.equal(JSON.stringify(coordinates), snapshot);
  assert.equal(corpus.length, 6); assert.equal(plan.corpus.length, 8);
  assert.throws(() => space.plan([NaN, 0], corpus, 2, coordinates, pca, 3), /Invalid/);
  assert.throws(() => space.plan([0, 0], corpus, 2, coordinates, pca, 3), /Empty/);
});
