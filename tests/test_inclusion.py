import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import collect as c
import inclusion as i


def author(name,aff='',collective=False):
    return {'name':name,'affiliations':[aff] if aff else [],'collective':collective,'dceg_branches':[]}


def record(authors,status='unresolved',positions=None,review=None):
    r={'authors':authors,'status':status,'reason':'previous_first_author_assessment',
       'first_author_positions':positions or [1],'date_scope':'inside','evidence':[],
       'url':'https://pubmed.ncbi.nlm.nih.gov/example/'}
    if review:r['manual_review']=review
    return r


class InclusionTests(unittest.TestCase):
    def apply(self,r):return i.apply(r,c.affiliation_evidence)

    def test_last_dceg_with_external_first(self):
        r=self.apply(record([author('First','University X'),author('Last','DCEG, NCI')]))
        self.assertEqual(r['status'],'confirmed');self.assertEqual(r['inclusion_routes'],['dceg_last_author'])
        self.assertEqual(r['first_author_positions'],[1]);self.assertEqual(r['qualifying_author_positions'],[2])

    def test_nih_first_and_dceg_middle_fallback(self):
        r=self.apply(record([author('First','National Institute of Dental and Craniofacial Research'),author('Middle','DCEG, NCI'),author('Last','University X')]))
        self.assertEqual(r['inclusion_routes'],['dceg_coauthor_nih_first'])
        self.assertTrue(r['inclusion_decision']['first_author_nih_affiliation_verified'])

    def test_unverified_endpoint_does_not_block_fallback(self):
        r=record([author('First','National Institutes of Health'),author('Middle','DCEG, NCI'),author('Last','NIH')])
        r['authors'][-1]['dceg_branches']=['BB']
        self.assertEqual(self.apply(r)['inclusion_routes'],['dceg_coauthor_nih_first'])

    def test_outside_first_and_last_with_dceg_middle_stays_open(self):
        r=self.apply(record([author('First','University X'),author('Middle','DCEG, NCI'),author('Last','University Y')]))
        self.assertEqual(r['status'],'unresolved')

    def test_old_first_author_exclusion_is_not_new_policy_veto(self):
        r=self.apply(record([author('First','University X'),author('Last','DCEG, NCI')],status='excluded'))
        self.assertEqual(r['status'],'confirmed');self.assertEqual(r['original_first_author_decision']['status'],'excluded')

    def test_confirmed_paper_specific_membership_without_published_dceg(self):
        review={'status':'confirmed','qualifying_author_positions':[1],'note':'Researcher confirmed DCEG membership on this paper.',
                'evidence_url':'https://example.test/attestation','confirmation_basis':'researcher_attestation'}
        r=self.apply(record([author('First','University X'),author('Last','NIH')],status='confirmed',review=review))
        self.assertEqual(r['inclusion_routes'],['dceg_first_author']);self.assertEqual(r['confirmation_basis'],'researcher_attestation')

    def test_inferred_membership_is_not_verified(self):
        r=record([author('First','NIH'),author('Last','University X')],status='inferred')
        r['authors'][0]['dceg_branches']=['BB']
        self.assertEqual(self.apply(r)['status'],'inferred')

    def test_documented_cofirst_retained(self):
        r=self.apply(record([author('First','University X'),author('Co-first','DCEG, NCI'),author('Last','University Y')],positions=[1,2]))
        self.assertEqual(r['inclusion_routes'],['dceg_first_author'])

    def test_collective_last_does_not_promote_penultimate_author(self):
        r=self.apply(record([author('First','University X'),author('Penultimate','DCEG, NCI'),author('Consortium','DCEG, NCI',True)]))
        self.assertEqual(r['status'],'unresolved')

    def test_unknown_date_blocks_inclusion(self):
        r=record([author('First','DCEG, NCI')]);r['date_scope']='boundary'
        self.assertEqual(self.apply(r)['status'],'unresolved')

    def test_two_endpoint_routes_do_not_duplicate_paper(self):
        r=self.apply(record([author('First','DCEG, NCI'),author('Last','DCEG, NCI')]))
        self.assertEqual(r['inclusion_routes'],['dceg_first_author','dceg_last_author'])
        self.assertEqual(r['qualifying_author_positions'],[1,2])

    def test_evidence_snapshot_is_independent(self):
        r=record([author('First','University X'),author('Last','DCEG, NCI')]);r['evidence']=[{'text':'Prior assessment'}]
        old=copy.deepcopy(r['evidence']);r=self.apply(r)
        self.assertEqual(r['original_first_author_decision']['evidence'],old)
        self.assertNotEqual(r['evidence'],old)

    def test_dated_last_author_review_preserves_original_first_author_status(self):
        r=record([author('First','University X'),author('Last','NIH')])
        r['id']='example'
        r['author_membership_reviews']=[{'status':'confirmed','author_position':2,'author':'Last',
            'note':'Official dated DCEG appointment spans this publication.','evidence_url':'https://example.test/bio',
            'confirmation_basis':'verified_historical_membership'}]
        r=self.apply(r)
        self.assertEqual(r['inclusion_routes'],['dceg_last_author'])
        self.assertEqual(r['original_first_author_decision']['status'],'unresolved')

    def test_dated_membership_review_rejects_author_order_mismatch(self):
        r=record([author('First','University X'),author('Last','NIH')]);r['id']='example'
        r['author_membership_reviews']=[{'status':'confirmed','author_position':2,'author':'Different person'}]
        with self.assertRaises(ValueError):self.apply(r)


class NIHAffiliationTests(unittest.TestCase):
    def test_nih_units_and_normal_abbreviations(self):
        for aff in ['NIH, Bethesda','National Cancer Institute, Rockville, MD','NCI, Bethesda',
                    'National Institute of Environmental Health Sciences','NHLBI',
                    'Natl Inst Hlth, Bethesda','National Library of Medicine, Bethesda']:
            with self.subTest(aff=aff):self.assertIsNotNone(i.nih_affiliation(aff))

    def test_email_funding_former_and_designated_centers_do_not_qualify(self):
        for aff in ['University X. author@nih.gov','NIH-funded research center, University X',
                    'NCI-designated Cancer Center, University X','Formerly National Cancer Institute, Bethesda',
                    'University X, supported by NIH','Office of the Director, University X',
                    '1University X; 2NIH, Bethesda','National Cancer Institute, Mexico City, Mexico',
                    'National Cancer Institute, Thailand']:
            with self.subTest(aff=aff):self.assertIsNone(i.nih_affiliation(aff))


if __name__=='__main__':unittest.main()
