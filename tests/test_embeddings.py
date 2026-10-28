import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('embed_publications', Path(__file__).parents[1] / 'scripts/embed_publications.py')
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)

try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipIf(np is None, 'Embedding verification requires numpy')
class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE publications (id TEXT PRIMARY KEY,title TEXT,abstract TEXT,status TEXT)')
        self.db.executemany('INSERT INTO publications VALUES (?,?,?,?)', [
            ('pmid:1', 'Paper one', 'An abstract', 'confirmed'),
            ('pmid:2', 'Paper two', None, 'confirmed'),
            ('pmid:3', 'Unverified', 'Other abstract', 'unresolved'),
            ('pmid:4', 'Excluded', None, 'excluded'),
            ('pmid:5', 'Inferred', 'Other abstract', 'inferred'),
        ])
        self.db.executescript(e.SCHEMA)
        for pid, title, abstract in e.confirmed(self.db):
            vector = np.zeros(e.DIMENSIONS, dtype='<f4')
            vector[int(pid[-1])] = 1
            self.db.execute('INSERT INTO publication_embeddings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (pid, e.MODEL_ID, e.REVISION, e.input_hash(e.document(title, abstract)),
                 e.FORMAT, int(bool(abstract)), 10, e.DIMENSIONS, 'float32-le', 1,
                 vector.tobytes(), '2026-10-23T00:00:00+00:00'))

    def tearDown(self):
        self.db.close()

    def test_confirmed_only_and_export_alignment(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            e.validate_and_export(self.db, output)
            with np.load(output / 'confirmed_embeddinggemma2.npz', allow_pickle=False) as data:
                self.assertEqual(data['publication_ids'].tolist(), ['pmid:1', 'pmid:2'])
                self.assertEqual(data['embeddings'].shape, (2, 768))
                self.assertEqual(data['embeddings'][:, 1:3].tolist(), [[1, 0], [0, 1]])
                meta = json.loads(str(data['metadata_json']))
                self.assertEqual((meta['with_abstract'], meta['title_only']), (1, 1))

    def test_stale_source_is_rejected(self):
        self.db.execute("UPDATE publications SET abstract='Changed abstract' WHERE id='pmid:1'")
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'Stale embedding: pmid:1'):
                e.validate_and_export(self.db, Path(folder))

    def test_newly_unconfirmed_record_is_rejected(self):
        self.db.execute("UPDATE publications SET status='unresolved' WHERE id='pmid:1'")
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'IDs do not match'):
                e.validate_and_export(self.db, Path(folder))

    def test_nonfinite_vector_is_rejected(self):
        vector = np.full(768, np.nan, dtype='<f4')
        self.db.execute("UPDATE publication_embeddings SET embedding=? WHERE publication_id='pmid:1'", (vector.tobytes(),))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'Invalid vector'):
                e.validate_and_export(self.db, Path(folder))

    def test_foreign_key_enforces_publication_link(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE publication_embeddings SET publication_id='missing' WHERE publication_id='pmid:1'")


if __name__ == '__main__':
    unittest.main()
