import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import investigate as i


def document(authors,tail=''):
    return ET.fromstring('<root><article><front><article-meta><title-group><article-title>Example</article-title></title-group><contrib-group>'+authors+'</contrib-group>'+tail+'</article-meta></front></article></root>')


def author(first,last,aff='',xref=''):
    return '<contrib contrib-type="author"><name><surname>'+last+'</surname><given-names>'+first+'</given-names></name>'+('<aff>'+aff+'</aff>' if aff else '')+xref+'</contrib>'


class InvestigationTests(unittest.TestCase):
    def test_different_full_names_do_not_match_same_initial(self):
        self.assertFalse(i.compatible({'last_name':'Li','first_name':'Xiaoming'},{'last_name':'Li','first_name':'Xiaohong'}))

    def test_conflicting_orcids_override_name_similarity(self):
        self.assertFalse(i.compatible({'last_name':'Li','first_name':'Xiaoming','orcid':'1'},{'last_name':'Li','first_name':'Xiaoming','orcid':'2'}))

    def test_unlinked_named_correspondence_identifies_author(self):
        f=i.frontmatter(document(author('Eleanor L.','Watts','University of Oxford'),'<author-notes><corresp id="cor1">Correspondence Eleanor L. Watts, Division of Cancer Epidemiology and Genetics, National Cancer Institute. eleanor@nih.gov</corresp></author-notes>'))
        self.assertTrue(any('Division of Cancer Epidemiology' in a for a in f['authors'][0]['affiliations']))

    def test_coauthor_correspondence_not_assigned_to_first_author(self):
        f=i.frontmatter(document(author('Ada','Example','University X')+author('Jane','Other','NCI','<xref ref-type="corresp" rid="cor1"/>'),'<author-notes><corresp id="cor1">Jane Other, DCEG, NCI. jane@nih.gov</corresp></author-notes>'))
        self.assertEqual(f['authors'][0]['affiliations'],['University X'])
        self.assertTrue(any('DCEG' in a for a in f['authors'][1]['affiliations']))

    def test_shared_affiliation_tail_resolves_three_branch_entries(self):
        a=author('Jessica','Madrigal','', '<xref ref-type="aff" rid="a1"/>')
        tail='<aff id="a1"><label>1</label>Occupational and Environmental Epidemiology Branch,</aff><aff id="a2"><label>2</label>Metabolic Epidemiology Branch, and</aff><aff id="a3"><label>3</label>Biostatistics Branch, Division of Cancer Epidemiology and Genetics, National Cancer Institute.</aff>'
        f=i.frontmatter(document(a,tail))
        self.assertIn('Division of Cancer Epidemiology',f['authors'][0]['affiliations'][0])
        self.assertTrue(f['affiliation_reconstructions'])

    def test_first_and_senior_notes_remain_separate(self):
        a=author('Ada','Example','University X','<xref ref-type="author-notes" rid="f1"/>')+author('Jane','Other','DCEG, NCI','<xref ref-type="author-notes" rid="f1"/>')+author('Tom','Senior','University Y','<xref ref-type="author-notes" rid="f2"/>')
        f=i.frontmatter(document(a,'<author-notes><fn id="f1">Equally contributing authors</fn><fn id="f2">These authors jointly supervised this work.</fn></author-notes>'))
        self.assertEqual(f['first_groups'][0]['positions'],[1,2])
        r={'id':'example','authors':[{'first_name':'Ada','last_name':'Example'},{'first_name':'Jane','last_name':'Other'},{'first_name':'Tom','last_name':'Senior'}]}
        d=i.decide(r,f,'https://example.test')
        self.assertEqual(d['status'],'confirmed');self.assertEqual(d['qualifying_author_positions'],[2])

    def test_partial_branch_affiliation_is_not_outside_institution(self):
        a=author('Ada','Example','Clinical Genetics Branch and','<xref ref-type="author-notes" rid="f1"/>')+author('Jane','Other','University X','<xref ref-type="author-notes" rid="f1"/>')
        f=i.frontmatter(document(a,'<author-notes><fn id="f1">These authors contributed equally.</fn></author-notes>'))
        self.assertIsNone(i.decide({'id':'example','authors':[{'first_name':'Ada','last_name':'Example'},{'first_name':'Jane','last_name':'Other'}]},f,'https://example.test'))

    def test_common_bibliographic_abbreviations(self):
        self.assertEqual(i.c.affiliation_evidence('Natl Canc Inst, Div Canc Epidemiol & Genet, Rockville')['level'],'confirmed')
        self.assertEqual(i.c.affiliation_evidence('Natl Inst Hlth, Div Canc Epidemiol & Genet, Bethesda')['level'],'confirmed')

    def test_joint_divisions_and_attached_affiliation_number(self):
        for text in ['Divisions of Cancer Prevention and Cancer Epidemiology and Genetics, NCI',
                     '1Division of Cancer Epidemiology & Genetics, NCI',
                     'Divison of Cancer Epidemiology and Genetics, National Cancer Institute']:
            self.assertEqual(i.c.affiliation_evidence(text)['level'],'confirmed')

    def test_named_current_address_belongs_only_to_named_author(self):
        f=i.frontmatter(document(author('Jeanny H.','Wang','University X')+author('Jane','Other','University Y'),'<author-notes><fn id="f1">Jeanny H. Wang is now at the Division of Cancer Epidemiology and Genetics, National Cancer Institute.</fn></author-notes>'))
        self.assertTrue(any('Division of Cancer Epidemiology' in s for s in f['authors'][0]['affiliations']))
        self.assertEqual(f['authors'][1]['affiliations'],['University Y'])

    def test_numbered_all_author_affiliations_do_not_confirm_first_author(self):
        evidence=i.c.affiliation_evidence('1Division of Gastroenterology, University X; 2Division of Cancer Epidemiology and Genetics, National Cancer Institute')
        self.assertEqual(evidence['level'],'unresolved')

    def test_unlinked_named_first_note_does_not_include_senior_authors(self):
        f=i.frontmatter(document(author('Diana R.','Withrow','University X')+author('Joseph','Boyle','University Y')+author('Jane','Senior','DCEG, NCI'),'<author-notes><fn id="f1">Diana R. Withrow and Joseph Boyle Co‐first authors.</fn><fn id="f2">Jane Senior Co‐senior author.</fn></author-notes>'))
        self.assertEqual(f['first_groups'][0]['positions'],[1,2])


if __name__=='__main__':unittest.main()
