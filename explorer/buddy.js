/* Balanced, entropy-regularized OT with KL relative to the product measure. */
(function (root) {
  'use strict';
  function transport(cost, m, n, epsilon = .05, {tolerance = 1e-7, maxIterations = 10000} = {}) {
    if (!m || !n || cost.length !== m * n || !Number.isFinite(epsilon) || epsilon < .02 || epsilon > .2) throw new Error('Invalid transport inputs.');
    let minimum = Infinity;
    for (const c of cost) {if (!Number.isFinite(c) || c < 0 || c > 2 + 1e-6) throw new Error('Invalid embedding cost.'); minimum = Math.min(minimum, c);}
    // Shifting the whole cost leaves the coupling unchanged. In this bounded
    // cost/epsilon range the positive kernel stays safely above underflow.
    const kernel = Float64Array.from(cost, c => Math.exp(-(c - minimum) / epsilon));
    const u = new Float64Array(m).fill(1), v = new Float64Array(n).fill(1);
    const columns = new Float64Array(n);
    let residual = Infinity, iterations = 0;
    for (; iterations < maxIterations; iterations++) {
      for (let i = 0; i < m; i++) {
        let sum = 0;
        for (let j = 0; j < n; j++) sum += kernel[i * n + j] * v[j];
        u[i] = 1 / (m * sum);
      }
      columns.fill(0);
      for (let i = 0; i < m; i++) for (let j = 0; j < n; j++) columns[j] += kernel[i * n + j] * u[i];
      for (let j = 0; j < n; j++) v[j] = 1 / (n * columns[j]);
      if (iterations % 10 === 0 || iterations === maxIterations - 1) {
        residual = 0;
        for (let i = 0; i < m; i++) {
          let sum = 0;
          for (let j = 0; j < n; j++) sum += kernel[i * n + j] * v[j];
          residual = Math.max(residual, Math.abs(m * u[i] * sum - 1));
        }
        if (residual <= tolerance) break;
      }
    }
    // Full regularized OT objective, including epsilon * KL(P | a tensor b).
    // Using transport cost alone would not produce the Sinkhorn divergence.
    let logU = 0, logV = 0;
    for (const x of u) logU += Math.log(x) / m;
    for (const x of v) logV += Math.log(x) / n;
    const value = minimum + epsilon * (logU + logV + Math.log(m) + Math.log(n));
    return {value, residual, iterations: Math.min(iterations + 1, maxIterations), converged: residual <= tolerance && Number.isFinite(value)};
  }

  function createEngine({corpus, dimensions, baseCount, packedCosts}) {
    const selfCache = new Map(), pairCache = new Map(), extraCosts = new Map();
    function ground(i, j) {
      if (i === j) return 0;
      const a = Math.min(i, j), b = Math.max(i, j);
      if (packedCosts && b < baseCount) return packedCosts[a * (2 * baseCount - a - 1) / 2 + b - a - 1];
      const key = a + ':' + b;
      if (!extraCosts.has(key)) {
        let dot = 0;
        for (let k = 0; k < dimensions; k++) dot += corpus[a * dimensions + k] * corpus[b * dimensions + k];
        extraCosts.set(key, Math.max(0, Math.min(2, 1 - dot)));
      }
      return extraCosts.get(key);
    }
    function objective(a, b, epsilon) {
      const cost = new Float64Array(a.length * b.length);
      for (let i = 0; i < a.length; i++) for (let j = 0; j < b.length; j++) cost[i * b.length + j] = ground(a[i], b[j]);
      const result = transport(cost, a.length, b.length, epsilon);
      if (!result.converged) throw new Error('Transport did not converge to the required marginal tolerance.');
      return result.value;
    }
    const signature = a => [...a].sort((x, y) => x - y).join(',');
    function self(a, key, epsilon) {
      const cacheKey = epsilon + '|' + key;
      if (!selfCache.has(cacheKey)) selfCache.set(cacheKey, objective(a, a, epsilon));
      return selfCache.get(cacheKey);
    }
    function divergence(a, b, epsilon = .05) {
      if (!a.length || !b.length) throw new Error('An author has no papers in this collection.');
      const keyA = signature(a), keyB = signature(b);
      if (keyA === keyB) return 0;
      const key = epsilon + '|' + [keyA, keyB].sort().join('|');
      if (!pairCache.has(key)) {
        const value = objective(a, b, epsilon) - .5 * self(a, keyA, epsilon) - .5 * self(b, keyB, epsilon);
        if (value < -1e-6 || !Number.isFinite(value)) throw new Error('Transport produced an unreliable divergence.');
        pairCache.set(key, Math.max(0, value));
      }
      return pairCache.get(key);
    }
    return {ground, divergence};
  }

  function profiles(papers, authorTypes) {
    const index = new Map();
    papers.forEach((paper, i) => {
      for (const name of new Set(paper.authors)) {
        if (authorTypes[name] === 'group') continue;
        if (!index.has(name)) index.set(name, []);
        index.get(name).push(i);
      }
    });
    return index;
  }
  const api = {transport, createEngine, profiles};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PaperBuddy = api;
})(globalThis);
