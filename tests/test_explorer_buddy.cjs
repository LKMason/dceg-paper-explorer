const assert = require('node:assert/strict');
const {test} = require('node:test');
const {transport, createEngine, profiles} = require('../explorer/buddy.js');
const near = (a, b, tolerance = 1e-8) => assert.ok(Math.abs(a - b) < tolerance, `${a} differs from ${b}`);

test('regularized OT includes KL entropy, matching a closed-form two-point solution', () => {
  for (const epsilon of [.02, .05, .1]) {
    const result = transport([0, .7, .7, 0], 2, 2, epsilon);
    assert.equal(result.converged, true);
    near(result.value, epsilon * Math.log(2 / (1 + Math.exp(-.7 / epsilon))));
    const transportCostAlone = .7 / (1 + Math.exp(.7 / epsilon));
    assert.ok(Math.abs(result.value - transportCostAlone) > .01);
  }
});

test('general two-by-two OT agrees with the analytical optimal coupling and KL objective', () => {
  const costs = [.15, .4, .7, .2], epsilon = .1;
  const t = .5 / (1 + Math.exp((costs[0] + costs[3] - costs[1] - costs[2]) / (2 * epsilon)));
  const plan = [t, .5 - t, .5 - t, t];
  const expected = plan.reduce((sum, p, i) => sum + p * (costs[i] + epsilon * Math.log(p / .25)), 0);
  const result = transport(costs, 2, 2, epsilon);
  assert.ok(result.converged); near(result.value, expected, 1e-7);
});

test('debiased divergence is zero on self, symmetric, and uses full embeddings', () => {
  const corpus = new Float32Array([1, 0, 0, 1, -1, 0, 0, -1]);
  const engine = createEngine({corpus, dimensions: 2, baseCount: 4});
  const a = [0, 1], b = [2, 3];
  near(engine.divergence(a, a), 0);
  near(engine.divergence(a, [1, 0]), 0);
  near(engine.divergence(a, b), engine.divergence(b, a));
  assert.ok(engine.divergence(a, b) > .1);
  near(engine.divergence([0], [2]), 2);
});

test('duplicated locations retain uniform probability mass, independently of portfolio size', () => {
  const corpus = new Float32Array([1, 0, 0, 1, 1, 0, 0, 1]);
  const engine = createEngine({corpus, dimensions: 2, baseCount: 4});
  near(engine.divergence([0, 1], [0, 1, 2, 3]), 0);
});

test('portfolios with the same centroid but different shape are distinguished', () => {
  const engine = createEngine({corpus: new Float32Array([1, 0, -1, 0, 0, 1, 0, -1]), dimensions: 2, baseCount: 4});
  assert.ok(engine.divergence([0, 1], [2, 3]) > .5);
});

test('packed symmetric base costs and dynamically added vectors have consistent indexing', () => {
  const engine = createEngine({corpus: new Float32Array([1, 0, 0, 1, -1, 0, 1, 0]), dimensions: 2,
    baseCount: 3, packedCosts: new Float32Array([1, 2, 1])});
  near(engine.ground(0, 2), 2); near(engine.ground(2, 0), 2);
  near(engine.ground(0, 3), 0); near(engine.ground(1, 3), 1);
  near(engine.divergence([0, 1], [3, 1]), 0);
});

test('invalid data is rejected and non-convergence is explicit', () => {
  assert.throws(() => transport([NaN], 1, 1), /Invalid embedding cost/);
  assert.throws(() => transport([0], 0, 1), /Invalid transport/);
  assert.throws(() => transport([0], 1, 1, 0), /Invalid transport/);
  const result = transport([0, .1, .8, .2], 2, 2, .05, {maxIterations: 1, tolerance: 1e-14});
  assert.equal(result.converged, false); assert.ok(result.residual > 1e-14);
});

test('author profiles use all papers, count each paper once, and exclude source-flagged collectives', () => {
  const index = profiles([{authors: ['A', 'A', 'B', 'Study Group']}, {authors: ['A', 'Unclassified Author']}], {'Study Group': 'group'});
  assert.deepEqual(index.get('A'), [0, 1]); assert.deepEqual(index.get('B'), [0]);
  assert.equal(index.has('Study Group'), false); assert.deepEqual(index.get('Unclassified Author'), [1]);
});
