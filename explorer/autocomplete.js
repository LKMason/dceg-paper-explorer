/* Source metadata distinguishes individual, collective, and unclassified authors. */
(function (root) {
  'use strict';
  function normalize(text) {
    return text.trim().toLocaleLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  }

  function authorSuggestions(authorIndex, authorTypes, query, limit = 4) {
    const key = normalize(query);
    if (!key) return [];
    return [...authorIndex.keys()]
      .map(name => ({name, key: normalize(name), kind: authorTypes[name] === 'group' ? 'group'
        : authorTypes[name] === 'person' ? 'researcher' : 'author'}))
      .filter(author => author.key.includes(key))
      .sort((a, b) => Number(!a.key.startsWith(key)) - Number(!b.key.startsWith(key))
        || authorIndex.get(b.name).length - authorIndex.get(a.name).length || a.name.localeCompare(b.name))
      .slice(0, limit);
  }

  const api = {authorSuggestions};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PaperAutocomplete = api;
})(globalThis);
