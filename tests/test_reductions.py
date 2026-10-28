import sqlite3
import unittest

from scripts import embed_publications as e
from scripts import reduce_embeddings as r

try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipIf(np is None, 'Reduction checks require numpy')
class ReductionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE publications(id TEXT PRIMARY KEY,title TEXT,abstract TEXT,status TEXT)')
        self.db.executemany('INSERT INTO publications VALUES(?,?,?,?)', [
            ('p2', 'Second', None, 'confirmed'),
            ('p1', 'First', 'An abstract', 'confirmed'),
            ('p3', 'Unverified', '', 'unresolved')])
        self.db.executescript(e.SCHEMA)
        for i, (pid, title, abstract) in enumerate(e.confirmed(self.db)):
            vector = np.zeros(768, dtype='<f4')
            vector[i] = 1
            self.db.execute('INSERT INTO publication_embeddings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (pid, e.MODEL_ID, e.REVISION, e.input_hash(e.document(title, abstract)),
                 e.FORMAT, int(bool(abstract)), 10, 768, 'float32-le', 1,
                 vector.tobytes(), '2026-10-23T00:00:00+00:00'))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_only_confirmed_inputs_in_stable_order(self):
        ids, vectors, digest = r.load_source(self.db)
        self.assertEqual(ids, ['p1', 'p2'])
        np.testing.assert_array_equal(vectors[:, :2], np.eye(2))
        self.assertEqual(digest, r.load_source(self.db)[2])

    def test_missing_or_stale_embedding_rejected(self):
        self.db.execute("UPDATE publications SET status='confirmed' WHERE id='p3'")
        with self.assertRaisesRegex(ValueError, 'Missing embedding: p3'):
            r.load_source(self.db)
        self.db.execute("UPDATE publications SET status='unresolved' WHERE id='p3'")
        self.db.execute("UPDATE publications SET title='Changed' WHERE id='p1'")
        with self.assertRaisesRegex(ValueError, 'Stale embedding: p1'):
            r.load_source(self.db)

    def test_invalid_coordinates_rejected(self):
        ids, _, _ = r.load_source(self.db)
        coordinates = {m: np.ones((2, 3)) for m in r.METHODS}
        coordinates['umap'][0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, 'Invalid umap'):
            r.validate_coordinates(ids, coordinates)
        coordinates['umap'] = np.ones((2, 2))
        with self.assertRaisesRegex(ValueError, 'Invalid umap'):
            r.validate_coordinates(ids, coordinates)

    def test_all_methods_round_trip_to_correct_papers(self):
        ids, _, digest = r.load_source(self.db)
        coordinates = {m: np.arange(6).reshape(2, 3).astype(float) + i*10
                       for i, m in enumerate(r.METHODS)}
        metadata = {'created_at': '2026-10-23', 'methods':
                    {m: {'run_id': m + ':test'} for m in r.METHODS}}
        r.store(self.db, ids, digest, coordinates, metadata)
        rows = self.db.execute('''SELECT r.method,c.publication_id,c.x,c.y,c.z
            FROM publication_embedding_reductions c JOIN embedding_reduction_runs r
            USING(run_id) ORDER BY r.method,c.publication_id''').fetchall()
        self.assertEqual(len(rows), 6)
        for method, pid, x, y, z in rows:
            self.assertEqual((x, y, z), tuple(coordinates[method][ids.index(pid)]))
        # A rerun of the same run IDs replaces coordinates instead of duplicating them.
        r.store(self.db, ids, digest, coordinates, metadata)
        self.assertEqual(self.db.execute('SELECT count(*) FROM publication_embedding_reductions').fetchone()[0], 6)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE publication_embedding_reductions SET publication_id='missing'")

    def test_changed_source_does_not_commit_reductions(self):
        ids, _, digest = r.load_source(self.db)
        coordinates = {m: np.ones((2, 3)) for m in r.METHODS}
        self.db.execute("UPDATE publications SET status='unresolved' WHERE id='p2'")
        metadata = {'created_at': '2026-10-23', 'methods':
                    {m: {'run_id': m + ':test'} for m in r.METHODS}}
        with self.assertRaisesRegex(ValueError, 'Source embeddings changed'):
            r.store(self.db, ids, digest, coordinates, metadata)
        self.assertEqual(self.db.execute('SELECT count(*) FROM embedding_reduction_runs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
