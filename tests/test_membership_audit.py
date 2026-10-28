import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from audit_membership import audit


def author(name, orcid='', affiliation='', branches=None):
    first, last = name.split(' ', 1)
    return {'name': name, 'first_name': first, 'last_name': last, 'orcid': orcid,
            'affiliations': [affiliation] if affiliation else [], 'dceg_branches': branches or []}


def paper(key, authors, status='unresolved'):
    return {'id': key, 'title': key, 'url': 'https://example.org/' + key,
            'publication_date': '2023', 'status': status, 'reason': 'test', 'authors': authors}


class MembershipAuditTests(unittest.TestCase):
    def test_same_name_alone_is_insufficient(self):
        target = paper('target', [author('Lee Mason', affiliation='University X')])
        ref = paper('ref', [author('Lee Mason', affiliation='DCEG, NCI')], 'confirmed')
        self.assertEqual(audit([target, ref]), [])

    def test_coauthor_network_finds_external_only_joint_appointment_candidate(self):
        coauthors = [author('Jane One'), author('Alex Two')]
        target = paper('target', [author('Lee Mason', affiliation='University X')] + coauthors)
        ref = paper('ref', [author('Lee Mason', affiliation='DCEG, NCI')] + coauthors, 'confirmed')
        lead = audit([target, ref])[0]
        self.assertEqual(lead['lead_type'], 'name_and_coauthor_network')
        self.assertEqual(target['status'], 'unresolved')
        self.assertEqual(target['authors'][0]['affiliations'], ['University X'])

    def test_orcid_conflict_overrides_identical_names_and_coauthors(self):
        coauthors = [author('Jane One'), author('Alex Two')]
        target = paper('target', [author('Wei Tang', '0001')] + coauthors)
        ref = paper('ref', [author('Wei Tang', '0002', 'DCEG, NCI')] + coauthors, 'confirmed')
        self.assertEqual(audit([target, ref]), [])

    def test_catalogue_annotation_with_other_unit_stays_a_flagged_lead(self):
        target = paper('target', [author('Wei Tang', affiliation='Center for Cancer Research, NCI', branches=['LTG'])])
        lead = audit([target])[0]
        self.assertTrue(lead['other_nih_unit_flag'])
        self.assertEqual(lead['references'], [])
        self.assertEqual(target['status'], 'unresolved')


if __name__ == '__main__':
    unittest.main()
