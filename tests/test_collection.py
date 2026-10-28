import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('collect', Path(__file__).parents[1]/'scripts'/'collect.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def author(aff='', equal=None, branches=None, collective=False):
    return {'name':'Example Researcher','affiliations':[aff] if aff else [],
            'equal_contrib':equal,'dceg_branches':branches or [],'collective':collective}


class EligibilityTests(unittest.TestCase):
    def test_explicit_dceg_first(self):
        self.assertEqual(c.evaluate({'authors':[author('Division of Cancer Epidemiology & Genetics, NCI')]})[0],'confirmed')

    def test_branch_plus_nci(self):
        self.assertEqual(c.affiliation_evidence('Occupational & Environmental Epidemiology Branch, National Cancer Institute')['level'],'confirmed')

    def test_branch_without_institution_not_proof(self):
        self.assertIsNone(c.affiliation_evidence('Biostatistics Branch, Some University'))

    def test_other_division_not_dceg(self):
        self.assertIsNone(c.affiliation_evidence('Division of Cancer Epidemiology, German Cancer Research Center'))

    def test_nci_alone_needs_review(self):
        self.assertEqual(c.evaluate({'authors':[author('National Cancer Institute')]})[0],'unresolved')

    def test_official_author_annotation_supports_inference(self):
        self.assertEqual(c.evaluate({'authors':[author('NIH',branches=['TDRP/OEEB'])]})[0],'inferred')

    def test_annotation_with_outside_affiliation_does_not_qualify(self):
        self.assertEqual(c.evaluate({'authors':[author('University of Oxford',branches=['TDRP/OEEB'])]})[0],'unresolved')

    def test_middle_author_not_confirmed(self):
        self.assertEqual(c.evaluate({'authors':[author('University X'),author('DCEG, NCI')]})[0],'unresolved')

    def test_cofirst_dceg_qualifies(self):
        status,reason,_,positions=c.evaluate({'authors':[author('University X','Y'),author('DCEG, NCI','Y'),author('University Y')]})
        self.assertEqual((status,reason,positions),('confirmed','structured_cofirst_dceg',[1,2]))

    def test_equal_senior_author_not_first(self):
        self.assertEqual(c.evaluate({'authors':[author('University X'),author('University Y','Y'),author('DCEG, NCI','Y')]})[0],'unresolved')

    def test_multiple_equal_groups_need_review(self):
        self.assertEqual(c.evaluate({'authors':[author('University X','Y'),author('DCEG, NCI','Y'),author('University Z'),author('NCI','Y')]})[0],'unresolved')

    def test_collective_first_author_needs_review(self):
        self.assertEqual(c.evaluate({'authors':[author('DCEG, NCI',collective=True)]})[0],'unresolved')


class DateTests(unittest.TestCase):
    def test_month_precision(self):
        d=c.date_interval('2020 Jan')
        self.assertEqual((d['value'],d['precision'],d['upper']),('2020-01','month','2020-01-31'))

    def test_earliest_online_date_excludes_2019(self):
        r={'dates':[c.date_interval('2020 Feb'),c.date_interval('2019 Dec 30')]}
        self.assertEqual(c.in_scope(r,'2020-01-01','2026-10-15'),'outside')

    def test_current_month_without_day_needs_review(self):
        self.assertEqual(c.in_scope({'dates':[c.date_interval('2026 Oct')]},'2020-01-01','2026-10-15'),'boundary')

    def test_no_date_needs_review(self):
        self.assertEqual(c.in_scope({'dates':[]},'2020-01-01','2026-10-15'),'unknown')

    def test_day(self):
        self.assertEqual(c.date_interval('2024 02 29')['value'],'2024-02-29')


class ParsingTests(unittest.TestCase):
    def test_catalogue_duplicates_preserve_both_source_ids(self):
        a={'dceg_id':'a','title':'Not Available','dates':[],'tags':[]}
        b={'dceg_id':'b','title':'Useful title','dates':[c.date_interval('2022 May')],'tags':[]}
        r=c.join_catalogue_duplicates(a,b)
        self.assertEqual(r['catalogue_ids'],['a','b'])
        self.assertEqual(r['title'],'Useful title')
        self.assertEqual(r['dates'][0]['value'],'2022-05')

    def test_book_chapter_uses_contribution_date_not_series_start(self):
        xml='''<PubmedBookArticle><BookDocument><PMID>12</PMID><Book><BookTitle>Series</BookTitle><PubDate><Year>1993</Year></PubDate></Book><ArticleTitle>New chapter</ArticleTitle><ContributionDate><Year>2025</Year><Month>5</Month><Day>29</Day></ContributionDate><AuthorList><Author><LastName>Example</LastName><ForeName>Ada</ForeName></Author></AuthorList><PublicationType>Review</PublicationType></BookDocument></PubmedBookArticle>'''
        p=c.from_pubmed(ET.fromstring(xml))
        self.assertEqual(p['dates'][0]['value'],'2025-05-29')
        self.assertEqual(p['publication_types'],['Review'])

    def test_merge_preserves_affiliation_sources_and_doi_difference(self):
        catalogue={'pmid':'1','doi':'10.1/old','dceg_id':'xyz','url':'https://example.test/catalogue',
                   'authors':[{'name':'Ada Example','first_name':'Ada','last_name':'Example','affiliations':['NCI'],
                               'affiliation_sources':{'NCI':['DCEG']},'dceg_branches':['TDRP/OEEB'],'researcher_id':'123'}],
                   'dates':[],'tags':[],'title':'Example','abstract':'','journal':''}
        pubmed={'pmid':'1','doi':'10.1/new','authors':[{'name':'Ada Example','first_name':'Ada','last_name':'Example',
                'affiliations':['DCEG, NCI'],'affiliation_sources':{'DCEG, NCI':['PubMed']}}],
                'dates':[],'tags':[],'title':'Example','abstract':'','journal':''}
        r=c.merge_records(catalogue,pubmed)
        self.assertEqual(r['doi'],'10.1/new')
        self.assertEqual(r['identifier_aliases'][0]['value'],'10.1/old')
        self.assertEqual(r['authors'][0]['affiliation_sources'],{'DCEG, NCI':['PubMed'],'NCI':['DCEG']})

    def test_inline_abstract_affiliation_links_and_equal_contrib(self):
        xml='''<PubmedArticle><MedlineCitation><PMID>42</PMID><Article>
        <ArticleTitle>A <i>test</i> title</ArticleTitle><Journal><Title>Journal</Title><JournalIssue><PubDate><Year>2020</Year><Month>Jan</Month></PubDate></JournalIssue></Journal>
        <AuthorList><Author EqualContrib="Y"><LastName>A</LastName><ForeName>Ada</ForeName><AffiliationInfo><Affiliation>University X</Affiliation></AffiliationInfo></Author>
        <Author EqualContrib="Y"><LastName>B</LastName><ForeName>Bob</ForeName><AffiliationInfo><Affiliation>DCEG, NCI</Affiliation></AffiliationInfo></Author></AuthorList>
        <Abstract><AbstractText Label="RESULTS">A <b>bold</b> result.</AbstractText></Abstract></Article>
        <KeywordList><Keyword>Cancer</Keyword></KeywordList></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/test</ArticleId></ArticleIdList></PubmedData></PubmedArticle>'''
        p=c.from_pubmed(ET.fromstring(xml))
        self.assertEqual(p['title'],'A test title')
        self.assertEqual(p['abstract'],'RESULTS: A bold result.')
        self.assertEqual(p['authors'][1]['affiliations'],['DCEG, NCI'])
        self.assertEqual(c.evaluate(p)[1],'structured_cofirst_dceg')


if __name__=='__main__':
    unittest.main()
