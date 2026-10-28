import base64
import csv
import json
import hashlib
from pathlib import Path
import tempfile
import unittest

try:
    import numpy as np
    from scripts import build_explorer as explorer
except ImportError:
    np = None


@unittest.skipIf(np is None, 'Explorer preparation requires numpy')
class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.branch_path = self.root / 'branches.json'
        self.branch_path.write_text(json.dumps({'branches': [{'code': 'REB', 'name': 'Radiation Epidemiology Branch'}]}))
        self.rows = [
            {'id': 'paper-b', 'title': 'Second paper', 'abstract': 'Full abstract B', 'authors': 'Bea Author; Shared Researcher',
             'publication_date': '2023-04-02', 'journal': 'Journal B', 'status': 'confirmed', 'evidence_json': '[{"branch":"REB"}]'},
            {'id': 'paper-c', 'title': 'Third paper', 'abstract': '', 'authors': 'Cy Author',
             'publication_date': '2022', 'journal': 'Journal C', 'status': 'confirmed', 'evidence_json': '[]'},
            {'id': 'paper-a', 'title': 'First paper', 'abstract': 'Full abstract A', 'authors': 'Ada Author; Shared Researcher',
             'publication_date': '2024-02', 'journal': 'Journal A', 'status': 'confirmed', 'evidence_json': '[{"branch":"REB"}]'},
        ]
        self.write_rows()
        self.meta = {'model_id': explorer.MODEL_ID, 'model_revision': explorer.REVISION, 'created_at': '2026-10-23'}
        self.ids = ['paper-a', 'paper-b', 'paper-c']
        self.vectors = np.zeros((3, 768), dtype=np.float32)
        self.vectors[0, 0] = 1
        self.vectors[1, :2] = [.8, .6]
        self.vectors[2, 1] = 1
        self.write_vectors()
        self.write_reductions()

    def tearDown(self):
        self.temp.cleanup()

    def write_rows(self):
        with (self.root / 'confirmed.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=self.rows[0].keys())
            writer.writeheader()
            writer.writerows(self.rows)

    def write_vectors(self):
        np.savez(self.root / 'confirmed_embeddinggemma2.npz', publication_ids=self.ids,
                 embeddings=self.vectors, metadata_json=json.dumps(self.meta))

    def write_reductions(self, ids=None, invalid=False):
        # Coordinates deliberately have a different row order from both source files.
        points = np.array([[30, 0, 0], [20, 0, 0], [10, 0, 0]], dtype=float)
        if invalid:
            points[0, 0] = np.nan
        digest = hashlib.sha256()
        rows = {r['id']: r for r in self.rows}
        for pid in sorted(self.ids):
            row = rows[pid]
            key = json.dumps([pid, explorer.input_hash(explorer.document(row['title'], row['abstract']))],
                             sort_keys=True, separators=(',', ':')).encode()
            digest.update(key + b'\n' + self.vectors[self.ids.index(pid)].astype('<f4').tobytes())
        np.savez(self.root / 'confirmed_embeddinggemma2_3d.npz',
                 publication_ids=ids or ['paper-c', 'paper-b', 'paper-a'],
                 umap=points, tsne=points + 100, pca=points + 200,
                 pca_components=np.eye(3, 768), pca_mean=np.zeros(768),
                 metadata_json=json.dumps({**self.meta, 'source_sha256': digest.hexdigest()}))

    def store(self):
        return explorer.PaperStore(self.root, self.branch_path)

    def test_join_uses_publication_ids_across_every_projection(self):
        store = self.store()
        self.assertEqual(store.ids, self.ids)
        self.assertEqual(store.coordinates['umap'][0], [10, 0, 0])
        self.assertEqual(store.coordinates['tsne'][1], [120, 100, 100])
        self.assertEqual(store.coordinates['pca'][2], [230, 200, 200])
        self.assertEqual(store.papers['paper-a']['abstract'], 'Full abstract A')
        self.assertEqual(store.papers['paper-a']['authors'], ['Ada Author', 'Shared Researcher'])

    def test_neighbors_use_source_cosine_distance_exclude_self(self):
        payload = explorer.build_payload(self.store(), ['a query'], self.vectors[:1], 2)
        self.assertEqual(payload['papers'][0]['neighbors'], [[1, .2], [2, 1.]])
        self.assertEqual(payload['papers'][2]['neighbors'], [[1, .4], [0, 1.]])
        decoded = np.frombuffer(base64.b64decode(payload['vectors']), dtype='<f4').reshape(3, 768)
        np.testing.assert_allclose(decoded, self.vectors, atol=1e-6)
        costs = np.frombuffer(base64.b64decode(payload['buddyCosts']), dtype='<f4')
        np.testing.assert_allclose(costs, [.2, 1., .4], atol=1e-6)

    def test_missing_branch_is_preserved_and_coauthors_remain_searchable(self):
        store = self.store()
        self.assertEqual(store.papers['paper-c']['branches'], [])
        self.assertEqual(set(store.author_index['Shared Researcher']), {'paper-a', 'paper-b'})
        self.assertEqual(set(store.branch_index['REB']), {'paper-a', 'paper-b'})

    def test_missing_projection_id_is_rejected(self):
        self.write_reductions(ids=['paper-c', 'paper-b', 'missing'])
        with self.assertRaisesRegex(ValueError, 'Projection IDs'):
            self.store()

    def test_nonfinite_coordinates_are_rejected(self):
        self.write_reductions(invalid=True)
        with self.assertRaisesRegex(ValueError, 'Invalid umap'):
            self.store()

    def test_wrong_model_and_non_unit_vectors_are_rejected(self):
        self.meta['model_revision'] = 'wrong'
        self.write_vectors()
        with self.assertRaisesRegex(ValueError, 'different model'):
            self.store()
        self.meta['model_revision'] = explorer.REVISION
        self.vectors[0] *= 2
        self.write_vectors()
        with self.assertRaisesRegex(ValueError, 'unit normalized'):
            self.store()

    def test_duplicate_metadata_and_unconfirmed_records_are_rejected(self):
        self.rows.append(self.rows[0])
        self.write_rows()
        with self.assertRaisesRegex(ValueError, 'Duplicate publication'):
            self.store()
        self.rows.pop()
        self.rows[0]['status'] = 'unresolved'
        self.write_rows()
        with self.assertRaisesRegex(ValueError, 'Non-confirmed'):
            self.store()

    def test_invalid_query_vectors_cannot_be_packaged(self):
        with self.assertRaisesRegex(ValueError, 'Invalid demo query'):
            explorer.build_payload(self.store(), ['query'], np.zeros((1, 768)), 2)

    def test_collective_author_flags_survive_the_export(self):
        metadata = self.root / 'confirmed.jsonl'
        metadata.write_text(json.dumps({'authors': [
            {'name': 'Oral Cancer GWAS', 'collective': False},
            {'name': 'Oral Cancer GWAS', 'collective': True},
            {'name': 'Lindsay M Morton', 'collective': False},
            {'name': 'Unclassified Name'},
        ]}) + '\n')
        payload = explorer.build_payload(self.store(), ['query'], self.vectors[:1], 2)
        self.assertEqual(payload['authorTypes']['Oral Cancer GWAS'], 'group')
        self.assertEqual(payload['authorTypes']['Lindsay M Morton'], 'person')
        self.assertNotIn('Unclassified Name', payload['authorTypes'])

    def test_changed_text_with_same_ids_cannot_use_stale_projections(self):
        self.rows[0]['abstract'] = 'A changed abstract'
        self.write_rows()
        with self.assertRaisesRegex(ValueError, 'Projection fingerprint'):
            self.store()

    def test_standalone_html_safely_embeds_paper_text(self):
        payload = explorer.build_payload(self.store(), ['query'], self.vectors[:1], 2)
        payload['papers'][0]['abstract'] = '</script><script>untrusted()</script>'
        html = explorer.render(payload)
        self.assertNotIn('</script><script>untrusted()', html)
        self.assertIn('\\u003c/script>', html)
        self.assertNotIn('/*__EXPLORER_', html)
        self.assertNotIn('<script src=', html)


if __name__ == '__main__':
    unittest.main()
