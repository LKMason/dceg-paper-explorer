#!/usr/bin/env python3
"""Build a self-contained browser explorer; Python is only needed at build time."""
import argparse
import base64
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import numpy as np

try:
    from .embed_publications import MODEL_ID, REVISION, DIMENSIONS, CONTEXT_TOKENS, document, input_hash
except ImportError:
    from embed_publications import MODEL_ID, REVISION, DIMENSIONS, CONTEXT_TOKENS, document, input_hash

ROOT = Path(__file__).resolve().parents[1]
BROWSER_MODEL = 'onnx-community/embeddinggemma-2-ONNX'
BROWSER_REVISION = 'daa72c51243991dfcaf9f9137d2c573d8f7790c0'


def load_author_types(path):
    """Preserve source collective-author flags; never infer a person from a name."""
    types = {}
    with Path(path).open() as stream:
        for line in stream:
            record = json.loads(line)
            for author in record.get('authors', []):
                name = author.get('name', '').strip()
                if not name:
                    continue
                if author.get('collective') is True:
                    types[name] = 'group'
                elif author.get('collective') is False:
                    types.setdefault(name, 'person')
    return types


class PaperStore:
    """Join metadata, projections, and source vectors by publication ID."""

    def __init__(self, exports, branch_config):
        self.branches = json.loads(Path(branch_config).read_text())['branches']
        exports = Path(exports)
        author_metadata = exports / 'confirmed.jsonl'
        self.author_types = load_author_types(author_metadata) if author_metadata.exists() else {}
        csv.field_size_limit(100_000_000)
        with (exports / 'confirmed.csv').open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.papers = {}
        self.author_index = defaultdict(list)
        self.branch_index = defaultdict(list)
        for row in rows:
            if row.get('status') != 'confirmed':
                raise ValueError(f"Non-confirmed publication in confirmed export: {row['id']}")
            if row['id'] in self.papers:
                raise ValueError(f"Duplicate publication ID: {row['id']}")
            authors = [a.strip() for a in row['authors'].split(';') if a.strip()]
            evidence = json.loads(row.get('evidence_json') or '[]')
            branches = sorted({e['branch'] for e in evidence if e.get('branch')})
            paper = {
                'id': row['id'], 'title': row['title'], 'abstract': row['abstract'],
                'authors': authors, 'firstAuthor': authors[0] if authors else 'Unknown author',
                'date': row['publication_date'], 'year': row['publication_date'][:4],
                'journal': row['journal'], 'branches': branches,
                'doi': row.get('doi', ''), 'pmid': row.get('pmid', ''),
                'url': row.get('url', ''), 'tags': row.get('tags', ''),
                'types': row.get('publication_types', ''),
            }
            self.papers[paper['id']] = paper
            for author in set(authors):
                self.author_index[author].append(paper['id'])
            for branch in branches:
                self.branch_index[branch].append(paper['id'])

        with np.load(exports / 'confirmed_embeddinggemma2.npz', allow_pickle=False) as source:
            self.ids = source['publication_ids'].tolist()
            self.vectors = np.asarray(source['embeddings'], dtype=np.float32)
            self.embedding_metadata = json.loads(str(source['metadata_json']))
        self.id_to_index = {pid: i for i, pid in enumerate(self.ids)}
        if len(self.id_to_index) != len(self.ids) or set(self.ids) != set(self.papers):
            raise ValueError('Publication metadata and embedding IDs do not match')
        if self.vectors.shape != (len(self.ids), DIMENSIONS) or not np.isfinite(self.vectors).all():
            raise ValueError('Invalid source embedding matrix')
        norms = np.linalg.norm(self.vectors, axis=1)
        if not np.allclose(norms, 1, atol=1e-5):
            raise ValueError('Source embeddings must be unit normalized')
        if (self.embedding_metadata.get('model_id'), self.embedding_metadata.get('model_revision')) != (MODEL_ID, REVISION):
            raise ValueError('Embedding export uses a different model or revision')
        digest = hashlib.sha256()
        for pid in sorted(self.ids):
            paper = self.papers[pid]
            text_hash = input_hash(document(paper['title'], paper['abstract']))
            key = json.dumps([pid, text_hash], sort_keys=True, separators=(',', ':')).encode()
            digest.update(key + b'\n' + self.vectors[self.id_to_index[pid]].astype('<f4').tobytes())
        self.source_fingerprint = digest.hexdigest()
        self.vectors /= norms[:, None]
        with np.load(exports / 'confirmed_embeddinggemma2_3d.npz', allow_pickle=False) as reduced:
            reduced_ids = reduced['publication_ids'].tolist()
            index = {pid: i for i, pid in enumerate(reduced_ids)}
            if len(index) != len(reduced_ids) or set(index) != set(self.ids):
                raise ValueError('Projection IDs do not match the source embeddings')
            alignment = [index[pid] for pid in self.ids]
            self.coordinates = {}
            for method in ('umap', 'tsne', 'pca'):
                points = reduced[method][alignment]
                if points.shape != (len(self.ids), 3) or not np.isfinite(points).all():
                    raise ValueError(f'Invalid {method} coordinates')
                self.coordinates[method] = points.astype(float).tolist()
            self.reduction_metadata = json.loads(str(reduced['metadata_json']))
            components = np.asarray(reduced['pca_components'], dtype=float)
            mean = np.asarray(reduced['pca_mean'], dtype=float)
            if (components.shape != (3, DIMENSIONS) or mean.shape != (DIMENSIONS,)
                    or not np.isfinite(components).all() or not np.isfinite(mean).all()):
                raise ValueError('Invalid fitted PCA transform')
            self.pca_transform = {'components': components.tolist(), 'mean': mean.tolist()}
        if (self.reduction_metadata.get('model_id'), self.reduction_metadata.get('model_revision')) != (MODEL_ID, REVISION):
            raise ValueError('Projections use a different model or revision')
        if self.reduction_metadata.get('source_sha256') != self.source_fingerprint:
            raise ValueError('Projection fingerprint does not match the current publication text and source vectors; regenerate the reductions')


def demo_queries(queries, model_path, cache_path, device=None):
    """Cache real query embeddings so the demo topics never need a download."""
    fingerprint = hashlib.sha256(json.dumps([MODEL_ID, REVISION, queries]).encode()).hexdigest()
    if cache_path.exists():
        with np.load(cache_path, allow_pickle=False) as cached:
            if str(cached['fingerprint']) == fingerprint:
                vectors = cached['vectors']
                if (vectors.shape == (len(queries), DIMENSIONS) and np.isfinite(vectors).all()
                        and np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)):
                    print('Reusing cached demo query embeddings', flush=True)
                    return vectors
    manifest = json.loads((model_path / 'download_provenance.json').read_text())
    if (manifest.get('model_id'), manifest.get('revision')) != (MODEL_ID, REVISION):
        raise ValueError('The cached model does not match the source embeddings')
    import torch
    from sentence_transformers import SentenceTransformer
    print('Embedding demo queries with the pinned local model…', flush=True)
    model = SentenceTransformer(
        str(model_path), device=device, local_files_only=True,
        config_kwargs={'vision_config': None, 'audio_config': None},
        model_kwargs={'dtype': torch.float32},
    )
    model.eval()
    model.max_seq_length = min(model.max_seq_length, CONTEXT_TOKENS)
    if model.get_embedding_dimension() != DIMENSIONS:
        raise ValueError('Query model dimensions differ from the source embeddings')
    vectors = model.encode(
        ['task: search result | query: ' + query for query in queries], prompt='',
        batch_size=4, normalize_embeddings=True, convert_to_numpy=True,
        show_progress_bar=True,
    )
    vectors = np.asarray(vectors, dtype='<f4')
    if vectors.shape != (len(queries), DIMENSIONS) or not np.isfinite(vectors).all():
        raise ValueError('Invalid demo query embeddings')
    if not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5):
        raise ValueError('Demo query vectors must be normalized')
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache_path, fingerprint=np.asarray(fingerprint), vectors=vectors)
    return vectors


def build_payload(store, queries, query_vectors, neighbor_count=10):
    query_vectors = np.asarray(query_vectors, dtype=np.float32)
    if (query_vectors.shape != (len(queries), DIMENSIONS) or not np.isfinite(query_vectors).all()
            or not np.allclose(np.linalg.norm(query_vectors, axis=1), 1, atol=1e-5)):
        raise ValueError('Invalid demo query vectors')
    if not 1 <= neighbor_count < len(store.ids):
        raise ValueError('Neighbor count must be between 1 and the collection size minus 1')
    distances = np.clip(1 - store.vectors @ store.vectors.T, 0, 2)
    np.fill_diagonal(distances, np.inf)
    order = np.argsort(distances, axis=1, kind='stable')[:, :neighbor_count]
    # One symmetric triangle; reuse the original-vector costs for offline OT.
    packed_costs = distances[np.triu_indices(len(store.ids), 1)].astype('<f4')
    papers = []
    for i, pid in enumerate(store.ids):
        paper = dict(store.papers[pid])
        paper['neighbors'] = [[int(j), round(float(distances[i, j]), 6)] for j in order[i]]
        papers.append(paper)
    return {
        'papers': papers, 'branches': store.branches, 'authorTypes': store.author_types,
        'coordinates': {name: np.round(points, 5).tolist() for name, points in store.coordinates.items()},
        'dimensions': DIMENSIONS,
        'vectors': base64.b64encode(store.vectors.astype('<f4').tobytes()).decode('ascii'),
        'buddyCosts': base64.b64encode(packed_costs.tobytes()).decode('ascii'),
        'queries': [{'text': q, 'vector': v.tolist()} for q, v in zip(queries, query_vectors)],
        'browserModel': {'model': BROWSER_MODEL, 'revision': BROWSER_REVISION},
        'pcaTransform': store.pca_transform, 'sourceFingerprint': store.source_fingerprint,
        'contextTokens': CONTEXT_TOKENS,
        'model': MODEL_ID, 'modelRevision': REVISION,
        'createdAt': store.reduction_metadata.get('created_at'),
    }


def render(payload, source=ROOT / 'explorer'):
    template = (source / 'index.template.html').read_text()
    # Opening the editable template goes to the finished app. Do not carry that
    # redirect into the generated, independently shareable HTML.
    prefix, marker, remainder = template.partition('<!--__TEMPLATE_REDIRECT_START__-->')
    if marker:
        _, end_marker, suffix = remainder.partition('<!--__TEMPLATE_REDIRECT_END__-->')
        if not end_marker:
            raise ValueError('Unclosed template redirect block')
        template = prefix + suffix
    # Escape all '<' in JSON so source text cannot terminate the data script.
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).replace('<', '\\u003c')
    contents = {
        '/*__EXPLORER_STYLES__*/': (source / 'styles.css').read_text(),
        '/*__EXPLORER_DATA__*/': encoded,
        '/*__SEARCH_WORKER__*/': (source / 'search-worker.js').read_text().replace('</script', '<\\/script'),
        '/*__EXPLORER_APP__*/': (source / 'app.js').read_text().replace('</script', '<\\/script'),
        '/*__EXPLORER_AUTOCOMPLETE__*/': (source / 'autocomplete.js').read_text().replace('</script', '<\\/script'),
        '/*__EXPLORER_MODES__*/': (source / 'modes.js').read_text().replace('</script', '<\\/script'),
        '/*__EXPLORER_PUBMED__*/': (source / 'pubmed.js').read_text().replace('</script', '<\\/script'),
        '/*__EXPLORER_SPACE__*/': (source / 'paper-space.js').read_text().replace('</script', '<\\/script'),
        '/*__BUDDY_MATH__*/': (source / 'buddy.js').read_text().replace('</script', '<\\/script'),
        '/*__BUDDY_WORKER__*/': (source / 'buddy-worker.js').read_text().replace('</script', '<\\/script'),
        '/*__BROWSER_RUNTIME__*/': (source / 'vendor/transformers.web.min.js').read_text().replace('</script', '<\\/script'),
        '<!--__THIRD_PARTY_NOTICES__-->': '<!--\nBundled @huggingface/transformers 4.3.1.\n'
            'The original two runtime imports were replaced with pinned browser module URLs.\n'
            + (source / 'vendor/TRANSFORMERS-LICENSE').read_text().replace('--', '—') + '\n-->',
    }
    for token, value in contents.items():
        if template.count(token) != 1:
            raise ValueError(f'Expected one template token: {token}')
        template = template.replace(token, value)
    return template


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exports', type=Path, default=ROOT / 'exports')
    parser.add_argument('--output', type=Path, default=ROOT / 'index.html')
    parser.add_argument('--model-path', type=Path, default=ROOT / '.cache/embeddinggemma-2')
    parser.add_argument('--device', choices=('cpu', 'mps', 'cuda'), default=None)
    parser.add_argument('--neighbors', type=int, default=10)
    args = parser.parse_args()
    store = PaperStore(args.exports, ROOT / 'config/affiliations.json')
    queries = json.loads((ROOT / 'config/explorer_queries.json').read_text())
    if not queries or any(not isinstance(q, str) or not q.strip() for q in queries):
        parser.error('Demo queries must be a nonempty list of strings')
    vectors = demo_queries(queries, args.model_path, ROOT / '.cache/explorer-query-vectors.npz', args.device)
    payload = build_payload(store, queries, vectors, args.neighbors)
    html = render(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html)
    print(f'Built {args.output} ({len(html.encode()) / 1e6:.1f} MB; {len(store.ids)} papers)', flush=True)
    print('Open this file directly in a browser. No server or Python process is needed.', flush=True)


if __name__ == '__main__':
    main()

