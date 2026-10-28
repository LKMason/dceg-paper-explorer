#!/usr/bin/env python3
"""Embed confirmed publications locally; checkpoint vectors in the source SQLite DB.

Only status='confirmed' is eligible. Inputs include the title and available abstract
using Google's titled-document format. No publication text is sent to a service.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = 'google/embeddinggemma-2'
REVISION = '914f7f89142e33e77833254d9c9b90c3cef7303b'
DIMENSIONS = 768
CONTEXT_TOKENS = 8192
FORMAT = 'title: {title} | text: {abstract}'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS publication_embeddings (
 publication_id TEXT NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
 model_id TEXT NOT NULL,
 model_revision TEXT NOT NULL,
 input_sha256 TEXT NOT NULL,
 input_format TEXT NOT NULL,
 has_abstract INTEGER NOT NULL CHECK(has_abstract IN (0,1)),
 token_count INTEGER NOT NULL,
 dimensions INTEGER NOT NULL CHECK(dimensions=768),
 dtype TEXT NOT NULL CHECK(dtype='float32-le'),
 normalized INTEGER NOT NULL CHECK(normalized=1),
 embedding BLOB NOT NULL CHECK(length(embedding)=3072),
 created_at TEXT NOT NULL,
 PRIMARY KEY(publication_id,model_id,model_revision)
);
'''


def document(title, abstract):
    return FORMAT.format(title=(title or '').strip() or 'none',
                         abstract=(abstract or '').strip())


def input_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def confirmed(db):
    return db.execute("SELECT id,title,abstract FROM publications WHERE status='confirmed' ORDER BY id").fetchall()


def validate_and_export(db, output):
    import numpy as np
    papers = confirmed(db)
    stored = {row[0]: row for row in db.execute('''
        SELECT publication_id,input_sha256,embedding,token_count,has_abstract
        FROM publication_embeddings WHERE model_id=? AND model_revision=?
    ''', (MODEL_ID, REVISION))}
    expected_ids = {p[0] for p in papers}
    if set(stored) != expected_ids:
        raise ValueError('Embedding IDs do not match the confirmed publication IDs')
    vectors, tokens = [], []
    for pid, title, abstract in papers:
        row = stored[pid]
        if row[1] != input_hash(document(title, abstract)):
            raise ValueError(f'Stale embedding: {pid}')
        vector = np.frombuffer(row[2], dtype='<f4')
        if vector.shape != (DIMENSIONS,) or not np.isfinite(vector).all():
            raise ValueError(f'Invalid vector: {pid}')
        if not np.isclose(np.linalg.norm(vector), 1.0, atol=1e-5):
            raise ValueError(f'Non-unit vector: {pid}')
        if row[4] != int(bool((abstract or '').strip())):
            raise ValueError(f'Abstract flag mismatch: {pid}')
        vectors.append(vector)
        tokens.append(row[3])
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Database foreign-key check failed')
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Database integrity check failed')
    matrix = np.stack(vectors) if vectors else np.empty((0, DIMENSIONS), dtype='<f4')
    ids = np.asarray([p[0] for p in papers], dtype=str)
    norms = np.linalg.norm(matrix, axis=1)
    metadata = {
        'model_id': MODEL_ID, 'model_revision': REVISION,
        'scope': "publications.status = 'confirmed'", 'count': len(papers),
        'with_abstract': sum(bool((p[2] or '').strip()) for p in papers),
        'title_only': sum(not bool((p[2] or '').strip()) for p in papers),
        'dimensions': DIMENSIONS, 'dtype': 'float32-le', 'normalized': True,
        'input_format': FORMAT, 'max_input_tokens': max(tokens, default=0),
        'truncated_inputs': 0,
        'norm_min': float(norms.min()) if len(norms) else None,
        'norm_max': float(norms.max()) if len(norms) else None,
        'verified_at': datetime.now(timezone.utc).isoformat(),
        'sqlite_table': 'publication_embeddings',
    }
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'confirmed_embeddinggemma2.npz'
    temp = output / 'confirmed_embeddinggemma2.tmp.npz'
    np.savez_compressed(temp, publication_ids=ids, embeddings=matrix,
                        metadata_json=np.asarray(json.dumps(metadata)))
    temp.replace(target)
    metadata['npz_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    (output / 'confirmed_embeddinggemma2.metadata.json').write_text(
        json.dumps(metadata, indent=2) + '\n')
    print(json.dumps(metadata, indent=2), flush=True)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data/publications.sqlite')
    parser.add_argument('--model-path', type=Path, default=ROOT / '.cache/embeddinggemma-2')
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--device', choices=['cpu', 'mps', 'cuda'], default=None)
    parser.add_argument('--output', type=Path, default=ROOT / 'exports')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error('--batch-size must be positive')
    if not args.db.is_file():
        parser.error('Database does not exist')
    db = sqlite3.connect(args.db)
    db.execute('PRAGMA foreign_keys=ON')
    if args.verify_only:
        validate_and_export(db, args.output)
        return
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    # A local snapshot is downloaded separately at this exact revision.
    manifest = json.loads((args.model_path / 'download_provenance.json').read_text())
    if manifest['model_id'] != MODEL_ID or manifest['revision'] != REVISION:
        raise ValueError('Local model provenance does not match the pinned model')
    print('Loading pinned local EmbeddingGemma 2 (text encoders only)', flush=True)
    model = SentenceTransformer(
        str(args.model_path), device=args.device, local_files_only=True,
        config_kwargs={'vision_config': None, 'audio_config': None},
        model_kwargs={'dtype': torch.float32},
    )
    model.eval()
    # The architecture supports longer sequences, but the embedding checkpoint's
    # documented context window is 8,192 tokens.
    model.max_seq_length = min(model.max_seq_length, CONTEXT_TOKENS)
    print(f'Device: {model.device}; max sequence length: {model.max_seq_length}', flush=True)
    if model.get_embedding_dimension() != DIMENSIONS:
        raise ValueError('Unexpected model embedding dimension')
    papers = confirmed(db)
    texts = [document(p[1], p[2]) for p in papers]
    # Count before encoding so no paper can be silently truncated.
    tokens = [len(model.tokenizer(t, truncation=False)['input_ids']) for t in texts]
    if max(tokens, default=0) > model.max_seq_length:
        raise ValueError('An input exceeds the model context; refusing to truncate')
    db.executescript(SCHEMA)
    # Eligibility can change between runs; remove this model's ineligible entries.
    db.execute('''DELETE FROM publication_embeddings WHERE model_id=?
        AND publication_id NOT IN (SELECT id FROM publications WHERE status='confirmed')''',
        (MODEL_ID,))
    db.commit()
    existing = dict(db.execute('''SELECT publication_id,input_sha256
        FROM publication_embeddings WHERE model_id=? AND model_revision=?''',
        (MODEL_ID, REVISION)))
    pending = [(p, t, n) for p, t, n in zip(papers, texts, tokens)
               if existing.get(p[0]) != input_hash(t)]
    # Similar-length batches reduce padding while IDs stay attached to the inputs.
    pending.sort(key=lambda item: item[2])
    print(f'{len(papers)} confirmed papers; {len(pending)} need embedding; '
          f'{sum(not bool((p[2] or "").strip()) for p in papers)} title-only; '
          f'longest input {max(tokens, default=0)} tokens', flush=True)
    started = time.monotonic()
    for offset in range(0, len(pending), args.batch_size):
        batch = pending[offset:offset + args.batch_size]
        # Explicit empty prompt prevents an extra default prefix on titled documents.
        vectors = model.encode([b[1] for b in batch], prompt='',
            batch_size=args.batch_size, normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=False)
        vectors = np.asarray(vectors, dtype='<f4')
        if vectors.shape != (len(batch), DIMENSIONS) or not np.isfinite(vectors).all():
            raise ValueError('Model produced invalid vectors')
        if not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5):
            raise ValueError('Model produced non-unit vectors')
        created = datetime.now(timezone.utc).isoformat()
        with db:
            for ((pid, title, abstract), text, n), vector in zip(batch, vectors):
                # Refuse a checkpoint if the source changed during inference.
                current = db.execute('SELECT title,abstract,status FROM publications WHERE id=?', (pid,)).fetchone()
                if current != (title, abstract, 'confirmed'):
                    raise ValueError(f'Publication changed during embedding: {pid}')
                db.execute('''INSERT OR REPLACE INTO publication_embeddings VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (pid, MODEL_ID, REVISION, input_hash(text), FORMAT,
                     int(bool((abstract or '').strip())), n, DIMENSIONS,
                     'float32-le', 1, vector.tobytes(), created))
        done = offset + len(batch)
        print(f'Embedded {done}/{len(pending)} ({time.monotonic()-started:.1f}s)', flush=True)
    validate_and_export(db, args.output)
    db.close()


if __name__ == '__main__':
    main()
