/* PaperBuddy is prepended by the app when constructing this independent worker. */
let buddyEngine, buddyProfiles;
function decodeBuddyFloats(encoded) {
  const raw = atob(encoded), bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  return new Float32Array(bytes.buffer);
}
async function findBuddies(message) {
  const {requestId, author, epsilon, minimumPapers, excludeCoauthors} = message;
  try {
    const source = buddyProfiles.get(author);
    if (!source) throw new Error('Choose an author represented in this collection.');
    const sourceSet = new Set(source);
    const candidates = [...buddyProfiles].filter(([name, papers]) => name !== author && papers.length >= minimumPapers);
    const results = [];
    let failed = 0, excluded = 0;
    for (let i = 0; i < candidates.length; i++) {
      const [name, papers] = candidates[i];
      const shared = papers.reduce((sum, index) => sum + Number(sourceSet.has(index)), 0);
      if (excludeCoauthors && shared) excluded++;
      else {
        try {results.push({name, count: papers.length, shared, divergence: buddyEngine.divergence(source, papers, epsilon)});}
        catch {failed++;}
      }
      if (i % 12 === 0) {
        postMessage({type: 'buddy-progress', requestId, text: `Comparing author portfolios · ${i + 1} / ${candidates.length}`});
        await new Promise(resolve => setTimeout(resolve, 0));
      }
    }
    results.sort((a, b) => a.divergence - b.divergence || a.name.localeCompare(b.name));
    postMessage({type: 'buddies', requestId, author, sourceCount: source.length, total: results.length, excluded, failed, results: results.slice(0, 30)});
  } catch (cause) {postMessage({type: 'buddy-error', requestId, text: cause.message});}
}
self.onmessage = ({data}) => {
  if (data.type === 'buddy-init') {
    const base = decodeBuddyFloats(data.vectors);
    const corpus = new Float32Array(base.length + data.additions.length * data.dimensions);
    corpus.set(base);
    data.additions.forEach((vector, i) => corpus.set(vector, base.length + i * data.dimensions));
    const packedCosts = data.costs ? decodeBuddyFloats(data.costs) : null;
    if (corpus.length !== data.papers.length * data.dimensions || packedCosts && packedCosts.length !== data.baseCount * (data.baseCount - 1) / 2) throw new Error('Invalid buddy corpus.');
    buddyEngine = PaperBuddy.createEngine({corpus, dimensions: data.dimensions, baseCount: data.baseCount, packedCosts});
    buddyProfiles = PaperBuddy.profiles(data.papers, data.authorTypes);
  } else if (data.type === 'buddy-find') findBuddies(data);
};
