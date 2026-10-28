#!/usr/bin/env python3
"""Compute and store 3D PCA, t-SNE and UMAP for confirmed publication embeddings."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sqlite3
import time

try:
    from .embed_publications import ROOT, MODEL_ID, REVISION, DIMENSIONS, document, input_hash
except ImportError:
    from embed_publications import ROOT, MODEL_ID, REVISION, DIMENSIONS, document, input_hash

SEED = 42
METHODS = ('pca', 'tsne', 'umap')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS embedding_reduction_runs (
 run_id TEXT PRIMARY KEY,
 method TEXT NOT NULL CHECK(method IN ('pca','tsne','umap')),
 model_id TEXT NOT NULL,
 model_revision TEXT NOT NULL,
 source_sha256 TEXT NOT NULL,
 publication_count INTEGER NOT NULL,
 dimensions INTEGER NOT NULL CHECK(dimensions=3),
 metadata_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publication_embedding_reductions (
 run_id TEXT NOT NULL REFERENCES embedding_reduction_runs(run_id) ON DELETE CASCADE,
 publication_id TEXT NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
 x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,
 PRIMARY KEY(run_id,publication_id)
);
'''


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def load_source(db):
    """Require a current, valid vector for every confirmed paper, in stable ID order."""
    import numpy as np
    rows = db.execute('''SELECT p.id,p.title,p.abstract,e.input_sha256,e.embedding
        FROM publications p LEFT JOIN publication_embeddings e
        ON e.publication_id=p.id AND e.model_id=? AND e.model_revision=?
        WHERE p.status='confirmed' ORDER BY p.id''', (MODEL_ID, REVISION)).fetchall()
    if not rows:
        raise ValueError('No confirmed publications')
    digest = hashlib.sha256()
    vectors = []
    for pid, title, abstract, stored_hash, blob in rows:
        if blob is None:
            raise ValueError(f'Missing embedding: {pid}')
        if stored_hash != input_hash(document(title, abstract)):
            raise ValueError(f'Stale embedding: {pid}')
        vector = np.frombuffer(blob, dtype='<f4')
        if vector.shape != (DIMENSIONS,) or not np.isfinite(vector).all():
            raise ValueError(f'Invalid embedding: {pid}')
        if not np.isclose(np.linalg.norm(vector), 1, atol=1e-5):
            raise ValueError(f'Non-unit embedding: {pid}')
        digest.update(canonical([pid, stored_hash]).encode() + b'\n' + blob)
        vectors.append(vector)
    return [r[0] for r in rows], np.stack(vectors), digest.hexdigest()


def validate_coordinates(ids, coordinates):
    import numpy as np
    if set(coordinates) != set(METHODS) or len(ids) != len(set(ids)):
        raise ValueError('Expected unique publication IDs and all three methods')
    for method, points in coordinates.items():
        if points.shape != (len(ids), 3) or not np.isfinite(points).all():
            raise ValueError(f'Invalid {method} coordinates')


def store(db, ids, source_hash, coordinates, metadata):
    """Commit all three methods together, after verifying that inputs are unchanged."""
    validate_coordinates(ids, coordinates)
    db.executescript(SCHEMA)
    with db:
        db.execute('BEGIN IMMEDIATE')
        current_ids, _, current_hash = load_source(db)
        if current_ids != ids or current_hash != source_hash:
            raise ValueError('Source embeddings changed during reduction')
        for method in METHODS:
            info = metadata['methods'][method]
            run_id = info['run_id']
            db.execute('DELETE FROM embedding_reduction_runs WHERE run_id=?', (run_id,))
            db.execute('INSERT INTO embedding_reduction_runs VALUES (?,?,?,?,?,?,?,?,?)',
                (run_id, method, MODEL_ID, REVISION, source_hash, len(ids), 3,
                 canonical({**info, 'software': metadata.get('software', {}),
                            'random_seed': metadata.get('random_seed', SEED)}),
                 metadata['created_at']))
            db.executemany('INSERT INTO publication_embedding_reductions VALUES (?,?,?,?,?)',
                [(run_id, pid, float(point[0]), float(point[1]), float(point[2]))
                 for pid, point in zip(ids, coordinates[method])])
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Database foreign-key check failed')
    for method in METHODS:
        rows = db.execute('''SELECT publication_id,x,y,z FROM publication_embedding_reductions
            WHERE run_id=? ORDER BY publication_id''',
            (metadata['methods'][method]['run_id'],)).fetchall()
        if [r[0] for r in rows] != ids or any(
                tuple(point) != tuple(row[1:]) for point, row in zip(coordinates[method], rows)):
            raise ValueError(f'{method} database round-trip failed')


def export(output, ids, coordinates, metadata, pca):
    import numpy as np
    output.mkdir(parents=True, exist_ok=True)
    stem = 'confirmed_embeddinggemma2_3d'
    npz_path = output / (stem + '.npz')
    temp = output / (stem + '.tmp.npz')
    np.savez_compressed(temp, publication_ids=np.asarray(ids, dtype=str),
        **coordinates, metadata_json=np.asarray(canonical(metadata)),
        pca_components=pca.components_[:3], pca_mean=pca.mean_,
        pca_explained_variance_ratio=pca.explained_variance_ratio_[:3])
    temp.replace(npz_path)
    csv_path = output / (stem + '.csv')
    temp = output / (stem + '.tmp.csv')
    with temp.open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['publication_id'] + [f'{m}_{axis}' for m in METHODS for axis in 'xyz'])
        writer.writerows([pid] + [float(x) for m in METHODS for x in coordinates[m][i]]
                         for i, pid in enumerate(ids))
    temp.replace(csv_path)
    metadata['exports'] = {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                           for p in (npz_path, csv_path)}
    (output / (stem + '.metadata.json')).write_text(json.dumps(metadata, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data/publications.sqlite')
    parser.add_argument('--output', type=Path, default=ROOT / 'exports')
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error('Database does not exist')
    import numpy as np
    from sklearn.decomposition import PCA
    from sklearn.manifold import TSNE, trustworthiness
    from threadpoolctl import threadpool_limits

    db = sqlite3.connect(args.db)
    db.execute('PRAGMA foreign_keys=ON')
    ids, vectors, source_hash = load_source(db)
    if len(ids) < 4:
        raise ValueError('At least four confirmed publications are needed')
    software = {name: importlib.metadata.version(name) for name in
        ('numpy', 'scipy', 'scikit-learn', 'umap-learn', 'numba', 'pynndescent')}
    metadata = {'model_id': MODEL_ID, 'model_revision': REVISION,
        'scope': "publications.status = 'confirmed'", 'count': len(ids),
        'source_dimensions': DIMENSIONS, 'dimensions': 3, 'source_sha256': source_hash,
        'created_at': datetime.now(timezone.utc).isoformat(), 'random_seed': SEED,
        'software': software, 'methods': {},
        'coordinate_table': 'publication_embedding_reductions',
        'run_table': 'embedding_reduction_runs',
        'coordinates_normalized': False}
    coordinates = {}
    started = time.monotonic()
    # Fixed thread count avoids oversubscription and makes reruns more reproducible.
    with threadpool_limits(limits=1):
        print(f'Loaded {len(ids)} confirmed embeddings. Fitting PCA...', flush=True)
        components = min(50, len(ids), DIMENSIONS)
        pca = PCA(n_components=components, svd_solver='full', whiten=False)
        scores = pca.fit_transform(vectors.astype(np.float64))
        coordinates['pca'] = scores[:, :3].copy()
        metadata['methods']['pca'] = {
            'parameters': {'n_components': 3, 'svd_solver': 'full', 'whiten': False,
                           'center': True, 'feature_scaling': False},
            'explained_variance_ratio': pca.explained_variance_ratio_[:3].tolist(),
            'explained_variance_ratio_total': float(pca.explained_variance_ratio_[:3].sum())}
        print('Fitting 3D t-SNE on 50-component PCA preprocessing...', flush=True)
        tsne_parameters = dict(n_components=3, perplexity=min(30.0, len(ids)-1.0),
            early_exaggeration=12.0, learning_rate='auto', max_iter=1500,
            n_iter_without_progress=300, min_grad_norm=1e-7, metric='euclidean',
            init='pca', random_state=SEED, method='barnes_hut', angle=0.5, n_jobs=1)
        tsne = TSNE(**tsne_parameters)
        coordinates['tsne'] = tsne.fit_transform(scores).astype(np.float64)
        metadata['methods']['tsne'] = {'parameters': tsne_parameters,
            'preprocessing': {'method': 'pca', 'n_components': components,
                'whiten': False, 'svd_solver': 'full',
                'explained_variance_ratio_total': float(pca.explained_variance_ratio_.sum())},
            'kl_divergence': float(tsne.kl_divergence_), 'iterations': int(tsne.n_iter_)}
        print(f'PCA and t-SNE done ({time.monotonic()-started:.1f}s). Loading UMAP...', flush=True)
        # Import lazily: first-run Numba compilation can take longer than PCA/t-SNE.
        from umap import UMAP
        umap_parameters = dict(n_components=3, n_neighbors=min(15, len(ids)-1),
            min_dist=0.1, spread=1.0, metric='cosine', output_metric='euclidean',
            n_epochs=500, init='spectral', random_state=SEED, transform_seed=SEED, n_jobs=1)
        print('Fitting 3D UMAP on original embeddings...', flush=True)
        coordinates['umap'] = UMAP(**umap_parameters).fit_transform(vectors).astype(np.float64)
        metadata['methods']['umap'] = {'parameters': umap_parameters, 'preprocessing': None}
        validate_coordinates(ids, coordinates)
        print('Checking local-neighborhood preservation and storing coordinates...', flush=True)
        for method in METHODS:
            info = metadata['methods'][method]
            k = min(15, (len(ids)-1)//2)
            info['trustworthiness'] = float(trustworthiness(vectors, coordinates[method],
                n_neighbors=k, metric='cosine'))
            info['trustworthiness_neighbors'] = k
            info['trustworthiness_source_metric'] = 'cosine'
            identity = {'method': method, 'source_sha256': source_hash,
                'parameters': info['parameters'], 'preprocessing': info.get('preprocessing'),
                'software': software, 'model_id': MODEL_ID, 'model_revision': REVISION}
            info['run_id'] = method + '3:' + hashlib.sha256(canonical(identity).encode()).hexdigest()
    store(db, ids, source_hash, coordinates, metadata)
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Database integrity check failed')
    export(args.output, ids, coordinates, metadata, pca)
    db.close()
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == '__main__':
    main()
