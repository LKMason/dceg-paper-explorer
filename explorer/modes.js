(function (root) {
  'use strict';
  function combineQueryResults(resultsA, resultsB, limit = 50) {
    const union = new Map();
    const limits = typeof limit === 'number' ? {a: limit, b: limit} : limit;
    for (const [side, results] of [['a', resultsA], ['b', resultsB]]) {
      results.slice(0, limits[side] ?? Infinity).forEach((result, rank) => {
        if (!union.has(result.index)) union.set(result.index, {index: result.index, similarityA: null, similarityB: null, rankA: null, rankB: null});
        const paper = union.get(result.index);
        const suffix = side.toUpperCase();
        if (paper['rank' + suffix] === null) {
          paper['rank' + suffix] = rank + 1;
          paper['similarity' + suffix] = result.similarity ?? null;
        }
      });
    }
    const counts = {all: union.size, a: 0, b: 0, both: 0};
    const results = [...union.values()].map(paper => {
      const membership = paper.rankA !== null && paper.rankB !== null ? 'both' : paper.rankA !== null ? 'a' : 'b';
      counts[membership]++;
      return {...paper, membership};
    }).sort((a, b) => Number(b.membership === 'both') - Number(a.membership === 'both')
      || Math.min(a.rankA ?? Infinity, a.rankB ?? Infinity) - Math.min(b.rankA ?? Infinity, b.rankB ?? Infinity)
      || a.index - b.index);
    return {results, counts};
  }
  const api = {combineQueryResults};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PaperModes = api;
})(globalThis);
