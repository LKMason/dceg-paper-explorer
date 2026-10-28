const assert = require('node:assert/strict');
const {test} = require('node:test');
const {authorSuggestions} = require('../explorer/autocomplete.js');

const index = new Map([
  ['Oral Cancer GWAS', [0]], ['Renal Cancer GWAS', [0]],
  ['Genetics and Epidemiology of Colorectal Cancer Consortium (GECCO)', [0]],
  ['Transdisciplinary Studies of Genetic Variation in Colorectal Cancer (CORECT)', [0]],
  ['Lindsay M Morton', [1, 2]], ['Raphaële Seror', [3]],
  ['Team Li', [4]], ['Unclassified Name', [5]],
]);
const types = Object.fromEntries([...index.keys()].map(name => [name,
  name === 'Unclassified Name' ? undefined : name.includes('GWAS') || name.includes('Colorectal') ? 'group' : 'person']));

test('GWAS suggestions are collective authors, never people', () => {
  const options = authorSuggestions(index, types, 'GWAS');
  assert.equal(options.length, 2);
  assert.ok(options.every(option => option.kind === 'group'));
});

test('Colorectal consortium suggestions retain their searchable published names', () => {
  const options = authorSuggestions(index, types, 'Colorectal');
  assert.equal(options.length, 2);
  assert.ok(options.every(option => option.kind === 'group' && index.has(option.name)));
});

test('individuals still match by surname and without diacritics', () => {
  assert.equal(authorSuggestions(index, types, 'morton')[0].kind, 'researcher');
  assert.equal(authorSuggestions(index, types, 'Raphaele')[0].name, 'Raphaële Seror');
});

test('source classification takes precedence over words in a name', () => {
  assert.equal(authorSuggestions(index, types, 'Team')[0].kind, 'researcher');
});

test('missing classification is not presented as a person', () => {
  assert.equal(authorSuggestions(index, types, 'Unclassified')[0].kind, 'author');
  assert.ok(authorSuggestions(index, {}, 'Morton').every(option => option.kind === 'author'));
});

test('empty queries return no author suggestions', () => {
  assert.deepEqual(authorSuggestions(index, types, '  '), []);
});
