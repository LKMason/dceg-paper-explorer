# DCEG publications, 2020 onward

A local, metadata-only database using the inclusion policy adopted October 22,
2026: a verified DCEG first/co-first or last-listed author qualifies; otherwise,
any verified DCEG author qualifies when the first-listed author reports an NIH
affiliation on the paper. Published affiliations, paper-specific verified
historical membership, and documented researcher attestations establish membership.
It includes a confirmed collection and
a separate pool of candidates awaiting review. **This first collection is not yet
certified complete.**

## Open the results

- Open `index.html` directly in a recent Chrome or Edge to launch the paper
  explorer. It is a single, self-contained client-side application; no server,
  installation, or running Python process is required.
- The original searchable database is still available at `exports/browse.html`.
  The older `scripts/browser.html` entry also forwards to that database.
- `exports/browse.html`: searchable, self-contained browser. Open it in a browser;
  it works offline and starts with confirmed publications selected.
- `data/publications.sqlite`: SQLite database, including full-text search over
  titles, abstracts, author names, and tags.
- `exports/confirmed.csv` and `exports/confirmed.jsonl`: confirmed records.
- `exports/review_queue.csv` and `.jsonl`: inferred and unresolved records.
- `exports/candidates.csv` and `.jsonl`: all candidates, with decision evidence.
- `exports/preprints.csv` and `.jsonl`: preprints, kept separate from the main
  publication collection as requested. Their affiliation assessment is retained
  in each JSONL record.
- `exports/conference_abstracts.csv` and `.jsonl`: conference abstracts kept in a
  separate collection for this pass; their underlying eligibility decisions are
  retained, including confirmed DCEG abstracts.
- `exports/coverage.json`: counts, missing fields, discovery gaps, and yearly totals.
- `exports/original_first_author.csv` and `.jsonl`: the original first/co-first
  subset, retained for comparison. Each record preserves its original decision.
- `exports/inclusion_policy_changes.csv` and `.jsonl`: changes from the original
  policy, with author positions and supporting evidence.
- `exports/inclusion_policy_summary.json`: policy, counts, and inclusion routes.
- `exports/staff_coverage_review.html`: October 22 staff-directory coverage audit,
  with new citations, resolved candidates, publishing aliases, and remaining leads.
- `exports/source_inventory.csv`: reconciliation inventory, including records
  outside the requested publication window.

The build template is `scripts/browser.template.html`; it is populated by the
export command and is not a standalone database browser.

The database contains title, abstract where supplied, authors in published order,
author affiliations, journal/venue, publication dates with precision, DOI/PMID,
publication types, source-backed MeSH and keyword tags, and eligibility evidence.
No AI-generated topic tags are mixed into the source tags. No article full text or
PDFs are requested or stored.

## Version control and local data

Git tracks the explorer source, configuration, tests, and the self-contained
`index.html`. The SQLite database, historical snapshots in `data/snapshots/`,
raw downloads, generated exports, model caches, and Python environment stay local
and are ignored. The explorer opens from a checkout without those local files;
rebuilding it requires the original exports described below. The optional older
publication browser also requires its local export.

Keep database backups separately from the Git repository when sharing or
archiving the collection. There is no need to split the standalone explorer:
its approximately 28 MB HTML file fits within GitHub's normal Git file-size limit.

## Paper explorer prototype

The root `index.html` contains the approved workstation design with a large
3D view, UMAP/t-SNE/PCA controls, search suggestions, and complete paper text and
metadata. It fills the browser window on laptop and desktop screens and reflows
on smaller screens. A mode switch keeps the plot, list panel, and paper inspector
in the same positions while changing their purpose:

- **Search:** highlight one query and show its ranked results directly in the
  list panel. Selecting a result or point opens one paper. Researcher, group
  author, and branch suggestions can apply exact metadata filters.
- **Neighbors:** click a point to select paper **A** and show its ten nearest
  papers. Each row shows an author/year citation, title, and cosine distance.
  Clicking a row adds **B** to the scrollable inspector; another row replaces B
  while A stays anchored. **Make A** moves the starting point to B. The search
  bar can find a starting paper, with candidate results under the result-count
  button. Neighborhood colors override other coloring: A is black, its nearest
  papers are blue, B is coral, and the rest of the collection is faded.
- **Compare:** use either search box to enter an idea or choose a researcher,
  group author, or branch suggestion. Semantic queries include their top 50;
  metadata filters include every exact match. The sets are colored blue
  for A only, coral for B only, and black for papers in both. The list shows each
  paper once, with filters and counts for All / A only / Both / B only. Shared
  papers appear first in the combined list. Selecting a paper shows its
  membership, semantic similarity scores, and exact-match labels. A paper absent
  from a semantic set is labeled outside its top 50; one absent from a metadata
  set is labeled not matched. Each search box shows the selected search type.
- **Add paper:** enter a PMID (or PubMed link) for any PubMed paper, including
  papers outside DCEG. Existing PMIDs are highlighted immediately. New records
  are retrieved directly from NCBI EFetch, embedded locally, and added to every
  mode. **Explore neighbors** opens their nearest papers. Missing abstracts use
  the title alone. Invalid or missing PMIDs and failed downloads are reported
  without adding a partial paper.
- **Paper buddy:** choose your own published author name or another author in
  the collection. The list ranks up to 30 buddies by debiased Sinkhorn divergence;
  selecting a buddy highlights their papers in coral, the source author's papers
  in blue, and shared papers in black. The inspector shows both portfolios with
  clickable paper titles. Candidate minimum (default three papers), smoothing
  epsilon (default 0.05), and an option to exclude existing coauthors are visible.

Queries, results, selections, and list filters are retained per mode while the
page stays open. The projection, camera, and plot expansion remain shared.
Delayed query results return to their originating mode and cannot overwrite
another mode or a newer search. Neighbor rankings and distances use the original
768-dimensional embeddings, independently of the selected projection.

The base collection, full abstracts and authors, projection coordinates,
source vectors, and neighbor lists are embedded in the HTML. No publication
data or search queries are sent to an API. The original publication links open
their source websites in a new tab.

New papers use the base pipeline's `title: {title} | text: {abstract}` document
format, the browser's pinned quantized EmbeddingGemma 2 model, and normalized
768-dimensional vectors. Text is never silently truncated; a record exceeding
the same 8,192-token context is rejected. Quantized document embeddings can differ
slightly from the base collection's full-precision vectors. Existing and new
papers' neighbor lists are updated from the full vectors, and active search and
comparison sets refresh after an addition.

PCA placement uses the original fitted components and mean. The saved UMAP and
t-SNE layouts have no exported transform, so new positions are **estimates**:
inverse-square cosine-distance interpolation over the ten nearest base papers.
Existing points stay fixed, and added-paper details identify estimated positions.
These coordinates are for display; neighbor distances always use the full vectors.

Additions are saved in browser local storage with their metadata and vectors,
keyed to the base dataset fingerprint and model revision. Refreshing restores
them without another lookup or embedding pass. A different browser, profile,
file location, or cleared storage may not share these additions. Storage errors
fall back to session-only additions with a visible notice; the HTML and source
exports are never modified by the browser. New lookups require internet access
to [NCBI EFetch](https://www.ncbi.nlm.nih.gov/books/NBK25499/#chapter4.EFetch).

### Browser search

Semantic search is the default. It compares query vectors with the original
normalized publication vectors and highlights the 50 nearest results in Search
mode. The ranked list occupies the list panel. Selecting a researcher, collective author,
or branch suggestion applies an exact metadata filter instead. Person and group
labels use the original author-level collective flags in `exports/confirmed.jsonl`;
unclassified entries are labeled simply as authors. Both Compare inputs offer the
same suggestions and can mix semantic searches with metadata filters. Search and
Compare use query colors; Neighbors always uses the selected paper's neighborhood.
Branch annotations are sparse, and a paper can have several. Author suggestions
use all published coauthors.

- **Offline demo topics:** the twelve topics under **Demo topics** have query
  vectors precomputed in Python using the exact pinned EmbeddingGemma 2 model.
  Their ranking runs in a browser worker and works completely offline, including
  when the HTML is opened directly from disk.
- **New free-text queries:** the first custom search downloads the ONNX Runtime
  assets, tokenizer, and quantized EmbeddingGemma 2 text model into the browser.
  Allow roughly 230 MB for that first download. A progress message appears while
  loading. Query inference and ranking run locally, using WebGPU when available
  and single-threaded WebAssembly otherwise. Cached assets and queries are
  reused where browser storage permits. If downloads or browser inference are
  unavailable, the offline topics, exact filters, map, and comparisons still work.
  Browser quantization can slightly change rankings compared with precomputed
  full-precision queries.

The browser uses Transformers.js **4.3.1**, with the matching ONNX Runtime
version pinned in `explorer/vendor/README.md`. The browser model is
`onnx-community/embeddinggemma-2-ONNX` at revision
`daa72c51243991dfcaf9f9137d2c573d8f7790c0`. Its
[model documentation](https://huggingface.co/onnx-community/embeddinggemma-2-ONNX)
describes the text-only loading, query prefix, normalized vectors, and q4
quantization used here.

### Paper buddy method

An author is the uniform probability measure over **all** their papers in this
collection (each paper has mass `1 / paper_count`). All eligible individual and
unclassified published author names are considered; collective authors are
excluded using the source flags, and the chosen author is excluded from their own
ranking. Profiles use all coauthored papers, not just first-author publications.
No centroid shortlist, subsampling, or top-paper cap is used. The paper minimum
applies to candidate buddies; a source author may have a single paper, with a
visible sparse-profile note.

The ground cost is cosine dissimilarity of normalized 768-dimensional embeddings,
equivalent to half squared Euclidean distance. The balanced regularized objective
is `OTε(A,B) = min_P <P,C> + ε KL(P || a⊗b)`, subject to the uniform marginals.
The ranking score is `Sε(A,B) = OTε(A,B) − ½ OTε(A,A) − ½ OTε(B,B)`, following
[Feydy et al. (2019)](https://proceedings.mlr.press/v89/feydy19a.html).
The entropy penalty is included in each OT value. Scores are divergences, not
percent similarity or a calibrated measure of collaboration potential.

A dedicated browser worker uses double-precision matrix scaling with a shifted
positive kernel. The epsilon range is bounded (0.05–0.20 in the UI); marginal
relative error must be at most `1e-7`, with at most 10,000 iterations. Any candidate
whose solve fails is omitted with an explicit count in the status, rather than
being assigned a false score. Numerical negative roundoff within `1e-6` is clamped
to zero. Cached self terms, pair scores, and identical portfolio signatures speed
up repeated queries. Results can be cancelled, and late results cannot overwrite
another mode. Adding a paper resets the buddy worker and updates existing results.

The build precomputes one triangle of the base cosine cost matrix as float32
(about 9 MB before base64 for this collection). New-paper costs are computed from
their vectors. This grows the standalone HTML to about 28 MB and lets buddy
matching run entirely offline without loading the embedding model. The plotted
3D coordinates are not used in these calculations. Existing coauthors and shared
papers are included by default, with overlap counts shown; identical portfolios
can tie at zero. The optional exclusion removes candidates with any shared paper.
Names are matched as published without identity disambiguation, and the available
papers are a collection-specific sample of each author's output.

### Rebuild after changing the data or interface

Edit `explorer/index.template.html`, `explorer/styles.css`, `explorer/app.js`,
`explorer/autocomplete.js`, `explorer/modes.js`, `explorer/pubmed.js`,
`explorer/paper-space.js`, `explorer/buddy.js`, `explorer/buddy-worker.js`, or
`explorer/search-worker.js`, then run:

```sh
.venv-embeddings/bin/python scripts/build_explorer.py
```

The builder reads `exports/confirmed.csv` and the original/reduced embedding NPZ
files, joins them by publication ID, validates shapes, finite coordinates,
model revisions, source fingerprints, and vector normalization, computes neighbor lists, and embeds
everything in `index.html` (about 28 MB, including buddy costs). It never modifies the collection or
source embeddings. Python and the dependencies already listed in
`requirements-embeddings.txt` are needed only for preparation. The first build
embeds the topics in `config/explorer_queries.json` using the cached local model;
subsequent builds reuse `.cache/explorer-query-vectors.npz` until those topics or
the source model revision change. Copy just `index.html` to share the explorer;
copy `exports/browse.html` as well if the older-browser link should remain usable.

```sh
.venv-embeddings/bin/python -m unittest discover -s tests -p test_explorer.py -v
npm ci
npm test
```

Node and jsdom are development-only test tools. The tests exercise overlap
membership, all three modes, the actual offline ranking worker, delayed responses,
cancellation, and author classification. The final HTML has no npm dependency
and requires no server.

Keyboard: focus the plot and use arrow keys to rotate, `+`/`-` to zoom, and `N`
to select the next paper. Search suggestions support arrow keys, Enter, and
Escape. The inspector and neighbor list scroll independently of the plot.

## Inclusion policy

The current definition is configured in `config/inclusion_policy.json` and
implemented in `scripts/inclusion.py`. A paper qualifies when:

1. A first-listed or documented co-first author, or the last-listed author, has
   verified DCEG membership around publication; or
2. Any author has verified DCEG membership and the first-listed author reports an
   NIH affiliation on the paper.

The second route can qualify even when endpoint membership is unclear. Published
author affiliations or paper-specific sourced reviews verify membership. An old
first-author exclusion does not veto a newly qualifying last-author or coauthor
route. Historical employment alone does not establish membership after departure.
No arbitrary grace period is added to appointment dates. Ambiguous timing remains
open. First and last positions refer to the published author list; a consortium
at an endpoint does not automatically promote the adjacent named author.

NIH institutional names and abbreviations are recorded in `config/nih_units.json`.
NIH email addresses, funding acknowledgments, NCI-designated university cancer
centers and explicitly former NIH addresses do not satisfy the NIH-first route.
Ambiguous international institutional names remain open. Cached PMC author
metadata are reused for all uniquely matched authors, including last authors.

`inclusion_decision` records each route, verified DCEG author positions, the
first-author NIH evidence and source links. `original_first_author_decision`
retains the previous classification and evidence. SQL view
`original_first_author_publications` and the browser's original-subset filter
expose that prior collection. `inclusion_decisions` stores the current policy
assessment before publication-type exclusions. Preprints and conference abstracts
remain separate from the main confirmed collection.

The publication window still ends October 15, 2026. The October 22 staff audit
adds individually reviewed PubMed citations within that window; it is not a full
refresh of all discovery searches. The database before this policy change is preserved at
`data/snapshots/publications-first-author-2026-10-15.sqlite`.

### Original first-author policy (retained for comparison)

Assess membership during the work, rather than using a person's current employer.
Any qualifying first author is sufficient; a former DCEG researcher can qualify
if the paper still reports their DCEG affiliation. Conversely, a joint appointee
or fellow may qualify even when the paper lists only a university or NIH, if
historical evidence or the researcher's attestation verifies DCEG membership.
Preserve the paper's actual affiliations; do not manufacture a published DCEG line.
If the researcher left DCEG before publication and lists only outside institutions,
require a paper-specific DCEG link or researcher confirmation. Former employment
alone does not establish eligibility.

- **Confirmed under the original policy:** the first-listed author, or a co-first author identified by an
  unambiguous initial group of PubMed equal-contribution flags, has an explicit
  DCEG affiliation or a verified DCEG branch name plus NCI/NIH institutional context.
  Individually reviewed records also qualify through sourced historical membership
  or documented first-author attestation. `confirmation_basis` distinguishes these
  routes in the database and exports.
- **Inferred:** a qualifying author's generic NCI/NIH affiliation is accompanied
  by an author-specific DCEG branch annotation in the official bibliography.
  These records are not included in confirmed exports.
- **Unresolved:** author roles, identity, affiliation, or publication dates need
  checking. A later DCEG author is not automatically counted as a co-first author,
  and absent equal-contribution flags are not taken as evidence against co-first
  authorship.
- **Excluded:** available for a sourced manual decision after review.

PubMed's equal-contribution flag does not identify distinct contribution groups.
A single contiguous flagged prefix beginning at author 1 is treated as structured
co-first evidence. Flags involving separated first/senior groups are left for
review. Group authorship at the first position also needs review.

The initial date policy uses the earliest known online or journal publication
date, rather than acceptance, indexing, or PMC release dates. Thus an article
published online in 2019 and printed in 2020 falls outside this window. Both dates
are retained. Year-only and month-only dates retain their actual precision; a date
that straddles a window boundary stays unresolved. This policy can be changed if a
print-year definition is preferred.

Letters, editorials, reviews, corrections, and other indexed publication types are
retained and labelled. The collection does not silently restrict itself to original
research articles. Preprints are excluded from the main confirmed collection and
retained in a separate export. Related versions are not merged solely by title.
Conference abstracts are also provisionally separated, pending the optional user
scope preference. This is controlled by `config/publication_scope.json`; changing
it and running `python3 scripts/apply_scope.py` reversibly updates the collection
without discarding eligibility evidence. Re-export with the same date window.

## Sources and discovery

1. [Official DCEG publications catalogue](https://dceg2.cancer.gov/publications/):
   fetch the entire metadata inventory to find undated entries, papers outside
   PubMed, author-specific branch annotations, and historical personnel clues.
   Older records are used for reconciliation, not included as 2020+ papers.
2. [PubMed E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25501/): annual
   affiliation searches for DCEG and its verified units, plus citation XML for the
   union of those results and relevant catalogue PMIDs.
3. Supplemental PubMed first-author searches seeded by names with DCEG branch
   annotations in relevant catalogue records, combined with broader NCI/NIH
   affiliation searches. Names are discovery clues, never automatic proof of
   DCEG affiliation.
4. [DCEG organization](https://dceg.cancer.gov/about/organization): the initial
   branch dictionary in `config/affiliations.json`. Historical aliases require
   separately sourced additions; current names alone are not a complete history.

Annual PubMed searches include the following calendar year to catch future print
issues with an already published online version; the local earliest-publication
date check determines eligibility. Each search must return its full advertised
count or collection fails rather than accepting truncation.

Records are joined by PMID, or a matching DOI when the catalogue lacks a PMID.
Author order comes from PubMed when available. Catalogue author annotations are
attached only after a unique exact normalized full-name match within the same
publication. Raw source metadata remains available to inspect discrepancies.

Every downloaded response is compressed in `data/raw/`, with its retrieval time,
URL, and SHA-256 digest beside it. `source_records` links database entries to these
cached responses. The public DCEG web client's API configuration is discovered at
runtime; access tokens are not hard-coded or written into provenance files.

## Run and rebuild

Requires Python 3.9+ with SQLite FTS5 and system `curl`. No pip dependencies.

```sh
python3 scripts/collect.py all --start 2020-01-01 --end 2026-10-15
python3 -m unittest discover -s tests -v
```

The commands can also be run separately:

```sh
python3 scripts/collect.py fetch --start 2020-01-01 --end 2026-10-15
python3 scripts/collect.py build --start 2020-01-01 --end 2026-10-15
```

Downloads are resumable: existing cached responses are reused. To refresh the
collection, move `data/raw` to a dated snapshot directory and run again. Reusing old
search pages is a snapshot rebuild, not a fresh search. The build writes a new
database and replaces the prior one only after SQLite integrity and foreign-key
checks succeed. The date window is checked against the discovery manifest.

## Confirmed-publication embeddings

`scripts/embed_publications.py` embeds **only `status = 'confirmed'`** records
using Google's [EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2).
It combines each title and abstract into one input with the model's recommended
document format: `title: {title} | text: {abstract}`. Missing abstracts are left
empty, producing a title-only embedding. Inferred, unresolved, and excluded
records are not embedded. Publication metadata and eligibility decisions are
unchanged.

The model revision is pinned in the script. Inference runs locally in float32
with the unused image and audio encoders disabled. Each paper receives one
L2-normalized, 768-dimensional vector. Inputs are checked against the model's
context limit before inference; the script refuses to silently truncate them.

Results are stored in `data/publications.sqlite`, table
`publication_embeddings`, joined to `publications.id` by the foreign key
`publication_id`. The `embedding` column is a 3,072-byte BLOB containing 768
little-endian float32 values. Model ID, revision, input SHA-256, abstract presence,
token count, normalization, format, and creation timestamp accompany each vector.
Batch checkpoints make interrupted runs resumable; changed title/abstract inputs
are re-embedded, and this model's embeddings for records no longer confirmed are
removed on the next run.

The exports are:

- `exports/confirmed_embeddinggemma2.npz`: `publication_ids` and `embeddings`
  arrays in matching row order, plus `metadata_json`. Load with
  `numpy.load(path, allow_pickle=False)`; the IDs join to the database and JSONL
  publication exports.
- `exports/confirmed_embeddinggemma2.metadata.json`: scope, counts, model revision,
  dimensions, token coverage, vector checks, and the NPZ file's SHA-256.

To install, download the pinned weights, and generate or resume embeddings:

```sh
python3 -m venv .venv-embeddings
.venv-embeddings/bin/python -m pip install -r requirements-embeddings.txt
.venv-embeddings/bin/python scripts/download_embedding_model.py
.venv-embeddings/bin/python scripts/embed_publications.py
```

`requirements-embeddings-lock.txt` records the complete installed dependency set
for the initial run (Python 3.14 on Apple silicon).

The first download requires Internet access and caches model assets under
`.cache/embeddinggemma-2/`; publication text is never uploaded. Subsequent
embedding runs use those local assets offline. `--device cpu` selects CPU;
otherwise Sentence Transformers selects the available accelerator. Use
`--batch-size 4` if device memory is limited.

To validate all associations, input hashes, vector dimensions, finite values,
unit norms, and database integrity, and refresh the exports without running the
model:

```sh
.venv-embeddings/bin/python scripts/embed_publications.py --verify-only
.venv-embeddings/bin/python -m unittest discover -s tests -p test_embeddings.py -v
```

Decode a stored vector with `numpy.frombuffer(row['embedding'], dtype='<f4')`.
For semantic search, embed queries with the same model and the prefix
`task: search result | query: `, then compare normalized vectors with a dot
product (cosine similarity).

The collection's `collect.py build` command replaces the database, including
derived embedding tables. Rerun the embedding script after rebuilding the
collection; its independent NPZ export remains available in `exports/`.

## Three-dimensional embedding reductions

`scripts/reduce_embeddings.py` computes 3D PCA, t-SNE, and UMAP coordinates for
every confirmed publication with a current EmbeddingGemma 2 vector. It rejects
missing, stale, or invalid source embeddings. The original 768-dimensional
embeddings remain unchanged.

The initial October 23 run covers 2,142 publications with each method:

| Method | Configuration | Initial result |
| --- | --- | --- |
| PCA | Centered original vectors, full SVD, no feature scaling or whitening | Three components retain 16.33% of variance |
| t-SNE | 50-component PCA preprocessing; perplexity 30; PCA initialization; automatic learning rate; 1,500 iterations | KL divergence 1.0313 |
| UMAP | Original vectors; cosine distance; 15 neighbors; minimum distance 0.1; 500 epochs | Three output coordinates per paper |

t-SNE and UMAP use seed 42 and one worker. The 50 PCA components supplied to
t-SNE retain 65.56% of the original variance. The standalone PCA coordinates are
the first three components from that same full-SVD fit. Coordinates are not
normalized after reduction. These are visualization coordinates; retain the
original vectors for full-dimensional semantic search.

`publication_embedding_reductions` stores `run_id`, `publication_id`, `x`, `y`,
and `z`. `embedding_reduction_runs` stores the method, original model ID and
revision, source fingerprint, population size, creation time, parameters,
diagnostics, and dependency versions. Join the two tables on `run_id`, then join
`publication_id` to `publications.id`. For example:

```sql
SELECT p.id, p.title, r.method, c.x, c.y, c.z
FROM publication_embedding_reductions AS c
JOIN embedding_reduction_runs AS r USING (run_id)
JOIN publications AS p ON p.id = c.publication_id;
```

Exports preserve the same publication IDs and row order across all methods:

- `exports/confirmed_embeddinggemma2_3d.npz`: `publication_ids`, `pca`, `tsne`,
  and `umap` arrays; each coordinate array has shape `(2142, 3)`. Includes
  `metadata_json`, the three PCA component vectors, PCA mean, and explained
  variance ratios. Load with `numpy.load(path, allow_pickle=False)`.
- `exports/confirmed_embeddinggemma2_3d.csv`: one row per publication, with
  `pca_x/pca_y/pca_z`, `tsne_x/tsne_y/tsne_z`, and `umap_x/umap_y/umap_z` columns.
- `exports/confirmed_embeddinggemma2_3d.metadata.json`: full parameters, source
  SHA-256, run IDs, software versions, file checksums, PCA explained variance,
  t-SNE KL divergence, and local-neighborhood trustworthiness for all methods
  (15 neighbors, original-space cosine distance).

To regenerate locally:

```sh
.venv-embeddings/bin/python -m pip install -r requirements-reductions.txt
NUMBA_CACHE_DIR="$PWD/.cache/numba" NUMBA_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 .venv-embeddings/bin/python scripts/reduce_embeddings.py
```

`requirements-reductions-lock.txt` records the full dependency set. Run IDs
depend on the source vectors, population, software versions, and parameters;
identical runs replace their coordinates, while changed runs are retained
separately in SQLite. The exports represent the most recent completed run.
Regenerate all reductions after changing source embeddings or confirmed
membership; the source fingerprint identifies the exact fitted population.
Rebuilding the collection database also removes these derived tables.

## Review workflow

Review `exports/review_queue.csv` or filter inferred/unresolved entries in the
browser. Check the publisher's author notes and affiliation list, using historical
DCEG identity evidence when the affiliation is generic. Record a decision in
`config/reviews.jsonl`, one JSON object per line, then rebuild. For example:

```json
{"id":"pmid:EXAMPLE","status":"confirmed","qualifying_author_positions":[2],"note":"Publisher author note identifies authors 1 and 2 as joint first; author 2 lists OEEB, NCI.","evidence_url":"https://publisher.example/article/author-information","reviewed_on":"2026-10-15"}
```

Use the actual publication ID, verified author positions, and evidence URL. A
confirmed decision requires an in-window publication date. Editing generated CSV
files does not change the database. Original automatic decisions remain in each
record, and manual decisions are stored in `review_decisions`.

## Query examples

```sql
SELECT title, journal, publication_date, doi
FROM confirmed_publications
ORDER BY publication_date DESC;

SELECT p.title, p.pmid
FROM publications_fts f
JOIN publications p ON p.id = f.publication_id
WHERE publications_fts MATCH 'pesticide'
  AND p.status = 'confirmed';

SELECT reason, COUNT(*) FROM review_queue GROUP BY reason;
```

## Remaining completeness work

The October 22 current-staff audit is reproduced with:

```sh
python3 scripts/audit_staff.py
python3 scripts/report_staff_review.py
```

`scripts/fetch_staff_profiles.py` caches the 229 linked public biographies;
`scripts/search_staff_gaps.py` searches unseeded directory names across all author
positions, and `scripts/search_staff_aliases.py` checks selected publishing-name
variants. Search results are leads and do not change eligibility. Retained
staff-audit citations are listed in `data/raw/discovery.json`, with source-specific
membership decisions in `config/author_membership_reviews.jsonl`. These decisions
verify a particular author at a particular publication and leave the original
first-author assessment intact. The pre-audit database is preserved in
`data/snapshots/publications-before-staff-audit-2026-10-22.sqlite`.

The staff report records failed biography requests, exact-name collisions,
appointment-date uncertainty, and papers predating DCEG appointments. A current
roster does not cover all personnel who left after 2020.

- Resolve missing and ambiguous co-first information against publisher author notes.
- Verify historical identities and DCEG membership for generic NCI/NIH affiliations.
- Expand beyond first-listed-author name discovery to find generic-affiliation
  co-first papers missing from the official catalogue.
- Reconcile non-PubMed publications absent from the DCEG catalogue against other
  bibliographic sources or an authoritative institutional export.
- Add historical branch names and check undated records and metadata conflicts.
- Audit samples of confirmed records, unresolved records, and source discrepancies.

The browser and `coverage.json` explicitly retain the incomplete designation until
these gaps have been assessed. Counts describe the collected evidence, not a claim
of exhaustive recall.

## Evidence-based candidate investigation

`scripts/investigate.py` ranks candidates using ORCID, compatible full names,
coauthor overlap, research topics, and publication history. These signals identify
promising matches; topic similarity and familiar collaborators alone never prove
DCEG affiliation or shared first authorship. Exact ORCID conflicts and incompatible
full given names prevent identity matches.

```sh
python3 scripts/investigate.py rank
python3 scripts/investigate.py investigate --limit 90
python3 scripts/investigate.py resolve-cached
python3 scripts/collect.py build --start 2020-01-01 --end 2026-10-15
```

The investigation uses PMC's **metadata-only** OAI-PMH `pmc_fm` format. It reads
author affiliations, contribution notes, and author-specific correspondence
addresses; it never requests article body text. Responses and provenance are saved
in `data/raw/frontmatter/`. Publisher notes can distinguish shared first authors
from shared senior authors. Bibliographic affiliation abbreviations and explicit
shared institutional tails across adjacent numbered branch entries are normalized.

Sourced automated decisions are stored in `config/automated_reviews.jsonl`.
Individually investigated historical or conflicting cases go in
`config/reviews.jsonl`, whose decisions take precedence. Identity corroboration
alone remains inferred. A reviewed, dated source establishing DCEG membership
during the work, or a documented researcher attestation, can confirm inclusion
without an explicit DCEG affiliation printed on the paper. The raw source,
reasoning, and original automatic assessment remain available. These earlier
review batches targeted first/co-first authors; the current endpoint policy is
evaluated separately and can qualify verified last-listed authors.

The frozen 283-candidate review is recorded in
`config/membership_review_batch_2026-10-15.json`, with sourced personnel findings
in `config/membership_author_findings.jsonl` and unverified search leads separately
in `config/membership_search_log.jsonl`. Reproduce its decisions with
`python3 scripts/review_membership_batch.py prepare`, rebuild the database, then
run `python3 scripts/review_membership_batch.py report`. The batch decisions take
precedence over automated reviews; individual `reviews.jsonl` decisions remain
the final override. `exports/membership_review.html` and
`exports/membership_review_results.csv` show every case and remaining evidence gap.

`python3 scripts/audit_membership.py` creates
`exports/membership_review_candidates.csv` and `.jsonl`. This audit also considers
external-only and absent affiliations: it uses author-specific catalogue branch
annotations, exact ORCIDs, and full-name matches with at least two shared coauthors.
It retains dated references and flags other NIH units. It never changes eligibility;
each lead still needs historical membership review. Counts are not an estimate of
how many eligible publications remain missing.

`exports/investigation_candidates.csv` records the prioritization signals;
`exports/frontmatter_investigation.jsonl` records the checked publisher metadata.
`exports/resolution_changes.csv` and `exports/resolution_summary.json` compare the
initial collection with the investigation results, distinguishing type-scope
changes from evidence-based decisions.
Respect [PMC's request guidance](https://pmc.ncbi.nlm.nih.gov/tools/oai/): no
concurrent requests, no more than three per second, and runs exceeding 100 requests
must occur outside peak hours. The initial investigation checked 98 unique records.
