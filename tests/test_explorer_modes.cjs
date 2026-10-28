const assert = require('node:assert/strict');
const {test} = require('node:test');
const {combineQueryResults} = require('../explorer/modes.js');
const row = (index, similarity = .8) => ({index, similarity});

test('pairwise membership separates exclusive and shared papers', () => {
  const {results, counts} = combineQueryResults([row(1), row(2), row(3)], [row(3, .7), row(4)]);
  assert.deepEqual(counts, {all: 4, a: 2, b: 1, both: 1});
  assert.equal(results[0].index, 3);
  assert.equal(results[0].membership, 'both');
  assert.equal(results[0].similarityA, .8);
  assert.equal(results[0].similarityB, .7);
  assert.equal(results.find(r => r.index === 4).similarityA, null);
});

test('identical queries have 50 shared papers and no exclusive members', () => {
  const input = Array.from({length: 80}, (_, i) => row(i, 1 - i / 100));
  assert.deepEqual(combineQueryResults(input, input).counts, {all: 50, a: 0, b: 0, both: 50});
});

test('only top-50 membership contributes to overlap', () => {
  const a = Array.from({length: 51}, (_, i) => row(i));
  const b = Array.from({length: 50}, (_, i) => row(i + 50));
  const combined = combineQueryResults(a, b);
  assert.deepEqual(combined.counts, {all: 100, a: 50, b: 50, both: 0});
  assert.equal(combined.results.find(r => r.index === 50).membership, 'b');
});

test('membership follows the query side and never mutates worker results', () => {
  const a = [row(8)], b = [row(4), row(8)];
  const copy = JSON.stringify([a, b]);
  assert.equal(combineQueryResults(a, b).results.find(r => r.index === 4).membership, 'b');
  assert.equal(combineQueryResults(b, a).results.find(r => r.index === 4).membership, 'a');
  assert.equal(JSON.stringify([a, b]), copy);
});

test('duplicate paper IDs are counted once per set', () => {
  assert.deepEqual(combineQueryResults([row(1), row(1)], [row(1)]).counts, {all: 1, a: 0, b: 0, both: 1});
});

test('exact metadata sets retain every match while semantic sets stay capped', () => {
  const exact = Array.from({length: 120}, (_, index) => ({index}));
  const semantic = Array.from({length: 80}, (_, i) => row(i + 70));
  const combined = combineQueryResults(exact, semantic, {a: Infinity, b: 50});
  assert.deepEqual(combined.counts, {all: 120, a: 70, b: 0, both: 50});
  assert.equal(combined.results.find(r => r.index === 119).membership, 'both');
  assert.equal(combined.results.find(r => r.index === 119).similarityA, null);
  assert.equal(combined.results.find(r => r.index === 119).similarityB, .8);
});

test('two exact sets use membership without inventing similarity scores', () => {
  const a = Array.from({length: 120}, (_, index) => ({index}));
  const b = a.filter(({index}) => index % 2);
  const combined = combineQueryResults(a, b, {a: Infinity, b: Infinity});
  assert.deepEqual(combined.counts, {all: 120, a: 60, b: 0, both: 60});
  assert.ok(combined.results.every(r => r.similarityA === null && r.similarityB === null));
});
