#!/usr/bin/env python3
"""Collect public citation metadata and build an auditable DCEG bibliography.

Python standard library only. Network transport uses system curl for its trusted
certificate store. No paper full-text or PDF endpoints are requested.
"""
import argparse
import calendar
from collections import Counter
import csv
from datetime import date, datetime, timezone
import gzip
from functools import lru_cache
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata
from urllib.parse import urlencode, urljoin
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data' / 'raw'
DB = ROOT / 'data' / 'publications.sqlite'
EXPORT = ROOT / 'exports'
CONFIG = json.loads((ROOT / 'config' / 'affiliations.json').read_text())
API = 'https://toolbox.imsweb.com/pubsearch-api/publications/dceg/advanced/'
EUTILS = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'


def now():
    return datetime.now(timezone.utc).isoformat()


def norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', ' ', s.replace('&', ' and ')).strip()


def expanded_affiliation(s):
    """Expand bibliographic abbreviations within affiliations, never author names."""
    n = norm(re.sub(r'^\s*\d+(?=[A-Za-z])',' ',s))
    replacements = {'natl':'national','canc':'cancer','inst':'institute','hlth':'health',
                    'div':'division','epidemiol':'epidemiology','genet':'genetics',
                    'clin':'clinical','radiat':'radiation','occupat':'occupational',
                    'environm':'environmental','biostat':'biostatistics','lab':'laboratory',
                    'translat':'translational','suscept':'susceptibility','metab':'metabolic',
                    'immunoepidemiol':'immunoepidemiology','integrat':'integrative','res':'research','divison':'division'}
    return ' '.join(replacements.get(w,w) for w in n.split())


def plain(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', str(s or '')))).strip()


def apply_publication_scope(r,status,reason):
    path=ROOT/'config'/'publication_scope.json'
    policy=json.loads(path.read_text()) if path.exists() else {}
    r['is_conference_abstract']=any(norm(t) in {'abstract','meeting abstract','conference abstract'} for t in r['publication_types'])
    if r['is_conference_abstract'] and policy.get('conference_abstracts')=='separate':
        if reason!='conference_abstract_kept_separate':
            r['eligibility_before_publication_scope']={'status':status,'reason':reason}
        return 'excluded','conference_abstract_kept_separate'
    if reason=='conference_abstract_kept_separate':
        prior=r['eligibility_before_publication_scope']
        return prior['status'],prior['reason']
    return status,reason


def curl(url, headers=None):
    # Passing configuration via stdin keeps the public web client's access token
    # out of command arguments, caches, logs, and source control.
    cmd = ['curl', '--fail', '--silent', '--show-error', '--location',
           '--connect-timeout', '20', '--max-time', '120', '--config', '-']
    config = ''.join('header = ' + json.dumps(k + ': ' + v) + '\n' for k, v in (headers or {}).items())
    for attempt in range(4):
        p = subprocess.run(cmd + [url], input=config.encode(), capture_output=True)
        if p.returncode == 0:
            return p.stdout
        if attempt == 3:
            raise RuntimeError(f'Public metadata request failed for {url.split("?")[0]}: {p.stderr.decode()[:200]}')
        time.sleep(2 ** attempt)


def cached(name, url, headers=None):
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / (name + '.gz')
    if path.exists():
        return gzip.decompress(path.read_bytes())
    data = curl(url, headers)
    # Refuse to cache a successful HTTP response that is actually an error page.
    if name.endswith('.json'):
        json.loads(data)
    elif name.endswith('.xml'):
        root = ET.fromstring(data)
        if root.find('.//ERROR') is not None:
            raise RuntimeError(ET.tostring(root, encoding='unicode')[:300])
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(gzip.compress(data, mtime=0))
    tmp.replace(path)
    (RAW / (name + '.provenance.json')).write_text(json.dumps({
        'retrieved_at': now(), 'url': url, 'sha256': hashlib.sha256(data).hexdigest(),
        'uncompressed_bytes': len(data)}, indent=2) + '\n')
    return data


def public_headers():
    page_url = 'https://dceg2.cancer.gov/publications/'
    page = curl(page_url).decode()
    bundle = re.search(r'<script[^>]+src="([^"]*/assets/[^\"]+\.js)"', page)
    if not bundle:
        raise RuntimeError('DCEG public client changed: cannot locate its JavaScript bundle')
    js = curl(urljoin(page_url, bundle.group(1))).decode()
    token = re.search(r'Authorization:"Token ([^"]+)"', js)
    if not token:
        raise RuntimeError('DCEG public client changed: cannot locate its public API configuration')
    return {'Authorization': 'Token ' + token.group(1),
            'Referer': 'https://dceg2.cancer.gov/', 'Origin': 'https://dceg2.cancer.gov'}


def dceg_records():
    records = []
    for p in sorted(RAW.glob('dceg-*.json.gz')):
        records.extend(json.loads(gzip.decompress(p.read_bytes()))['publications'])
    return records


def search_ids(label, term):
    url = EUTILS + 'esearch.fcgi?' + urlencode({
        'db': 'pubmed', 'term': term, 'retmode': 'json', 'retmax': 9999,
        'tool': 'dceg_metadata_bibliography'})
    j = json.loads(cached(label + '.json', url))['esearchresult']
    if 'error' in j or int(j['count']) != len(j['idlist']):
        raise RuntimeError('Search is truncated or invalid; split it into smaller date windows: ' + label)
    if j.get('warninglist', {}).get('phrasesnotfound'):
        print('Search warning:', label, j['warninglist'], flush=True)
    time.sleep(0.4)
    return j['idlist']


def date_interval(value):
    """Return earliest/latest possible day and honest display precision."""
    value = plain(value)
    y = re.search(r'\b(19\d{2}|20\d{2})\b', value)
    if not y:
        return None
    year = int(y.group(1))
    after = value[y.end():].strip(' -/')
    month = day = None
    numeric = re.match(r'^(\d{1,2})(?:[-/ ](\d{1,2}))?', after)
    if numeric:
        month = int(numeric.group(1))
        day = int(numeric.group(2)) if numeric.group(2) else None
    else:
        for m in range(1, 13):
            if re.match(calendar.month_abbr[m].lower(), after.lower()):
                month = m
                d = re.match(r'[A-Za-z]+\s+(\d{1,2})\b', after)
                day = int(d.group(1)) if d else None
                break
    try:
        lo = date(year, month or 1, day or 1)
        hi = date(year, month or 12, day or calendar.monthrange(year, month or 12)[1])
    except ValueError:
        return None
    display = f'{year:04}' + (f'-{month:02}' if month else '') + (f'-{day:02}' if day else '')
    # Seasonal/range dates retain their original text and need review at boundaries.
    return {'value': display, 'raw': value, 'precision': 'day' if day else 'month' if month else 'year',
            'lower': lo.isoformat(), 'upper': hi.isoformat()}


def in_scope(record, start, end):
    dates = [d for d in record['dates'] if d]
    if not dates:
        return 'unknown'
    # Earliest publication, never received/accepted/entrez/PMC deposit dates.
    lo = min(d['lower'] for d in dates)
    hi = min(d['upper'] for d in dates)
    if hi < start or lo > end:
        return 'outside'
    if lo < start or hi > end:
        return 'boundary'
    return 'inside'


def fetch(args):
    RAW.mkdir(parents=True, exist_ok=True)
    headers = public_headers()
    offset = 0
    total = None
    while total is None or offset < total:
        name = f'dceg-{offset:06}.json'
        url = API + '?' + urlencode({'b_size': 1000, 'b_start': offset, 'sort_on': 'date_author'})
        j = json.loads(cached(name, url, headers))
        total = int(j['metadata']['numresults'])
        pubs = j['publications']
        if not pubs:
            raise RuntimeError('DCEG pagination ended before its reported count')
        if int(j['metadata']['batch_start']) != offset:
            raise RuntimeError('DCEG returned the wrong page')
        offset += len(pubs)
        print(f'DCEG catalogue: {offset:,}/{total:,}', flush=True)
        time.sleep(0.2)
    records = dceg_records()
    uids = [p['UID'] for p in records]
    if len(uids) != total or len(set(uids)) != total:
        raise RuntimeError('DCEG inventory count/uniqueness check failed')
    aliases = ['"Division of Cancer Epidemiology"[ad]', 'DCEG[ad]']
    aliases += ['"' + b['name'] + '"[ad]' for b in CONFIG['branches']]
    discovery = '(' + ' OR '.join(aliases) + ')'
    all_ids = set()
    for year in range(int(args.start[:4]), int(args.end[:4]) + 2):
        term = discovery + f' AND ("{year}/01/01"[dp] : "{year}/12/31"[dp])'
        ids = search_ids(f'pubmed-affiliations-{year}', term)
        all_ids.update(ids)
        print(f'PubMed affiliation discovery {year}: {len(ids):,}', flush=True)
    # Add catalogue records even when indexed affiliations omit DCEG.
    for p in records:
        if p.get('pmid') and in_scope(from_dceg(p), args.start, args.end) != 'outside':
            all_ids.add(str(p['pmid']))
    # Supplemental discovery for known researchers with generic NCI/NIH affiliation.
    # Names generate candidates only; they never prove DCEG affiliation on a paper.
    seeds = set()
    for p in records:
        if in_scope(from_dceg(p), args.start, args.end) == 'outside':
            continue
        for a in p.get('authors', []):
            if a.get('branches') and a.get('lname') and a.get('fname'):
                seeds.add(a['lname'] + ' ' + a['fname'][0])
    seeds = sorted(seeds)
    generic = '("National Cancer Institute"[ad] OR "National Institutes of Health"[ad] OR NCI[ad] OR NIH[ad])'
    seed_hits = set()
    for i in range(0, len(seeds), 60):
        authors = '(' + ' OR '.join('"' + s.replace('"', '') + '"[1au]' for s in seeds[i:i+60]) + ')'
        term = authors + ' AND ' + generic + f' AND ("{args.start}"[dp] : "{int(args.end[:4])+1}/12/31"[dp])'
        ids = search_ids(f'pubmed-author-seeds-{i:04}', term)
        seed_hits.update(ids)
        all_ids.update(ids)
        print(f'Researcher-name discovery: {min(i+60,len(seeds))}/{len(seeds)} seeds, {len(seed_hits):,} hits', flush=True)
    (RAW / 'discovery.json').write_text(json.dumps({
        'start': args.start, 'end': args.end, 'created_at': now(),
        'dceg_inventory_count': total, 'seed_names': seeds,
        'seed_discovery_pmids': sorted(seed_hits), 'pmids': sorted(all_ids, key=int),
        'notes': 'First-author name searches are candidate discovery only; generic-affiliation co-first papers absent from the catalogue may still be missed.'}, indent=2))
    ids = sorted(all_ids, key=int)
    redirects = {}
    unavailable = []
    for i in range(0, len(ids), 150):
        batch = ids[i:i+150]
        key = hashlib.sha256(','.join(batch).encode()).hexdigest()[:16]
        url = EUTILS + 'efetch.fcgi?' + urlencode({
            'db': 'pubmed', 'id': ','.join(batch), 'retmode': 'xml', 'tool': 'dceg_metadata_bibliography'})
        xml = cached('pubmed-records-' + key + '.xml', url)
        root = ET.fromstring(xml)
        returned = {e.text for e in root.findall('./PubmedArticle/MedlineCitation/PMID')}
        returned |= {e.text for e in root.findall('./PubmedBookArticle/BookDocument/PMID')}
        if returned - set(batch):
            raise RuntimeError('PubMed batch unexpectedly returned unrequested identifiers')
        for missing in sorted(set(batch)-returned):
            single = ET.fromstring(cached(f'pubmed-single-{missing}.xml',EUTILS+'efetch.fcgi?'+urlencode({'db':'pubmed','id':missing,'retmode':'xml'})))
            if single.findall('PubmedArticle') or single.findall('PubmedBookArticle'):
                continue
            # Deleted duplicate PMIDs can disappear from EFetch while the public
            # citation page redirects to a replacement PMID (sometimes with a new DOI).
            page = cached(f'pubmed-redirect-{missing}.html',f'https://pubmed.ncbi.nlm.nih.gov/{missing}/').decode()
            canonical = re.search(r'<meta\s+name="citation_pmid"\s+content="(\d+)"',page)
            if not canonical:
                canonical = re.search(r'<link\s+rel="canonical"\s+href="https://pubmed.ncbi.nlm.nih.gov/(\d+)',page)
            if canonical and canonical.group(1) != missing:
                replacement = canonical.group(1)
                redirects[missing] = replacement
                cached(f'pubmed-single-{replacement}.xml',EUTILS+'efetch.fcgi?'+urlencode({'db':'pubmed','id':replacement,'retmode':'xml'}))
                print(f'PubMed duplicate identifier: {missing} → {replacement}',flush=True)
            else:
                unavailable.append(missing)
                print(f'PubMed identifier unavailable; retaining catalogue metadata: {missing}',flush=True)
        print(f'PubMed records: {min(i+150,len(ids)):,}/{len(ids):,}', flush=True)
        time.sleep(0.4)
    manifest = json.loads((RAW/'discovery.json').read_text())
    manifest['pmid_redirects'] = redirects
    manifest['unavailable_pmids'] = unavailable
    manifest['pmids'] = sorted(all_ids | set(redirects.values()),key=int)
    (RAW/'discovery.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Metadata acquisition complete.', flush=True)


def from_dceg(p):
    authors = []
    for a in p.get('authors', []):
        if a.get('investigator'):
            continue
        authors.append({'name': ' '.join(filter(None, [a.get('fname'), a.get('lname')])) or a.get('cname') or '',
                        'last_name': a.get('lname') or '', 'first_name': a.get('fname') or '',
                        'collective': bool(a.get('cname')), 'orcid': a.get('orcid') or '',
                        'affiliations': a.get('affiliations') or [],
                        'affiliation_sources':{s:['DCEG'] for s in (a.get('affiliations') or [])}, 'equal_contrib': None,
                        'dceg_branches': a.get('branches') or [], 'researcher_id': a.get('nid')})
    tags = []
    for m in p.get('mesh', []) or []:
        if isinstance(m, str):
            tags.append({'term': plain(m), 'source': 'DCEG:MeSH'})
        elif isinstance(m, dict):
            term = m.get('descriptorname') or m.get('descriptorName') or m.get('text') or m.get('descriptor')
            if isinstance(term, dict):
                term = term.get('text') or term.get('name')
            if term:
                tags.append({'term': plain(term), 'source': 'DCEG:MeSH'})
    abstract = '\n\n'.join((str(a.get('label')) + ': ' if a.get('label') else '') + plain(a.get('text'))
                            for a in p.get('abstract', []) if a.get('text'))
    dates = []
    for key, kind in [('edate', 'electronic'), ('pubdate', 'journal')]:
        d = date_interval(p.get(key))
        if d:
            dates.append({**d, 'kind': kind, 'source': 'DCEG'})
    # computed_date can fill absent publication dates but is explicitly labelled.
    if not dates and p.get('computed_date'):
        d = date_interval(p['computed_date'])
        if d:
            dates.append({**d, 'kind': 'catalogue_computed', 'source': 'DCEG'})
    return {'pmid': str(p.get('pmid') or ''), 'doi': (p.get('doi') or '').lower().strip(),
            'dceg_id': p.get('UID'), 'title': plain(p.get('Title')), 'abstract': abstract,
            'journal': plain(p.get('journal') or p.get('medlineta') or p.get('booktitle')),
            'publication_types': [p.get('psubtype') or p.get('citation_type') or 'Unknown'],
            'authors': authors, 'dates': dates, 'tags': tags, 'source': 'DCEG',
            'url': 'https://dceg2.cancer.gov/publications/#/?' + urlencode({'UID': json.dumps({'query':[p.get('UID')], 'operator':'or'})})}


def text_at(el, path):
    child = el.find(path)
    return plain(''.join(child.itertext())) if child is not None else ''


def xml_date(el):
    if el is None:
        return None
    medline = el.findtext('MedlineDate')
    if medline:
        return date_interval(medline)
    return date_interval(' '.join(filter(None, [el.findtext('Year'), el.findtext('Month'), el.findtext('Day')])))


def from_pubmed(el):
    book = el.tag == 'PubmedBookArticle'
    doc = el.find('BookDocument') if book else el.find('MedlineCitation')
    article = doc if book else doc.find('Article')
    pmid = doc.findtext('PMID')
    authors = []
    for a in article.findall('./AuthorList/Author'):
        authors.append({'name': ' '.join(filter(None, [a.findtext('ForeName') or a.findtext('Initials'), a.findtext('LastName')])) or text_at(a, 'CollectiveName'),
                        'last_name': a.findtext('LastName') or '', 'first_name': a.findtext('ForeName') or a.findtext('Initials') or '',
                        'collective': a.find('CollectiveName') is not None,
                        'orcid': next((i.text for i in a.findall('Identifier') if i.get('Source','').lower() == 'orcid'), '') or '',
                        'affiliations': [plain(''.join(f.itertext())) for f in a.findall('./AffiliationInfo/Affiliation')],
                        'affiliation_sources':{plain(''.join(f.itertext())):['PubMed'] for f in a.findall('./AffiliationInfo/Affiliation')},
                        'equal_contrib': a.get('EqualContrib'), 'dceg_branches': [], 'researcher_id': None})
    dates = []
    journal_date = xml_date(article.find('./Journal/JournalIssue/PubDate')) if not book else xml_date(article.find('ContributionDate'))
    if book and journal_date is None:
        journal_date = xml_date(article.find('./Book/PubDate'))
    if journal_date:
        dates.append({**journal_date, 'kind': 'journal', 'source': 'PubMed'})
    for d in article.findall('ArticleDate'):
        parsed = xml_date(d)
        if parsed:
            dates.append({**parsed, 'kind': d.get('DateType','article').lower(), 'source': 'PubMed'})
    history = el.find('./PubmedData/History')
    if history is not None:
        for d in history.findall('PubMedPubDate'):
            if d.get('PubStatus') in {'epublish','ppublish','aheadofprint'}:
                parsed = xml_date(d)
                if parsed:
                    dates.append({**parsed, 'kind': d.get('PubStatus'), 'source':'PubMed'})
    abs_parts = []
    for a in article.findall('./Abstract/AbstractText'):
        s = plain(''.join(a.itertext()))
        if s:
            abs_parts.append((a.get('Label') + ': ' if a.get('Label') else '') + s)
    tags = []
    for m in doc.findall('./MeshHeadingList/MeshHeading/DescriptorName'):
        tags.append({'term': plain(m.text), 'source':'PubMed:MeSH', 'identifier':m.get('UI'), 'major_topic':m.get('MajorTopicYN') == 'Y'})
    for k in doc.findall('./KeywordList/Keyword'):
        tags.append({'term': plain(''.join(k.itertext())), 'source':'PubMed:keyword'})
    doi = next((a.text for a in el.findall('./PubmedData/ArticleIdList/ArticleId') if a.get('IdType') == 'doi'), '')
    if not doi:
        doi = next((a.text for a in article.findall('ELocationID') if a.get('EIdType') == 'doi'), '')
    return {'pmid': pmid, 'doi': (doi or '').lower().strip(), 'dceg_id': None,
            'pmcid':next((a.text for a in el.findall('./PubmedData/ArticleIdList/ArticleId') if a.get('IdType')=='pmc'),None),
            'title': text_at(article, 'ArticleTitle') or text_at(article, 'Book/BookTitle'),
            'abstract':'\n\n'.join(abs_parts), 'journal':text_at(article, 'Journal/Title') or text_at(article, 'Book/BookTitle'),
            'publication_types':[plain(p.text) for p in article.findall('./PublicationTypeList/PublicationType')]+([plain(p.text) for p in article.findall('PublicationType')] if book else []),
            'authors':authors, 'dates':dates, 'tags':tags, 'source':'PubMed', 'url':'https://pubmed.ncbi.nlm.nih.gov/' + pmid + '/'}


@lru_cache(maxsize=200000)
def affiliation_evidence(s):
    if len(re.findall(r'(?:^|;)\s*\d+\s*(?=[A-Za-z])',s))>1:
        return {'level':'unresolved','kind':'unassigned_publisher_affiliations','text':s,'source':'published_affiliation'}
    n = expanded_affiliation(s)
    institutional = bool(re.search(r'\bnational cancer institute\b|\bnational institutes? (?:of )?health\b|\bnci\b|\bnih\b',n))
    full_division = bool(re.search(r'\bdivisions? (?:of )?(?:cancer prevention and )?cancer epidemiology (?:and )?genetics\b',n))
    abbreviated_division = bool(re.search(r'\bdiv(?:ision)? (?:of )?cancer epidemiol\w*',n))
    if re.search(r'\bdceg\b', n) or full_division or (institutional and abbreviated_division):
        return {'level':'confirmed', 'kind':'explicit_dceg', 'text':s, 'source':'published_affiliation'}
    if not institutional:
        return None
    for branch in CONFIG['branches']:
        if norm(branch['name']) in n and institutional:
            return {'level':'confirmed', 'kind':'dceg_branch_with_institution', 'text':s,
                    'branch':branch['code'], 'source':CONFIG['source']}
    if institutional:
        return {'level':'unresolved', 'kind':'generic_nci_nih', 'text':s, 'source':'published_affiliation'}
    return None


def evaluate(r):
    """Conservative automatic decisions; missing co-first evidence stays open."""
    authors = r['authors']
    matches = []
    for pos, a in enumerate(authors, 1):
        for s in a['affiliations']:
            e = affiliation_evidence(s)
            if e:
                matches.append({**e, 'author_position':pos, 'author':a['name'],
                                'metadata_sources':a.get('affiliation_sources',{}).get(s,[])})
    first_group = {1} if authors and not authors[0]['collective'] else set()
    equal_positions = [i for i,a in enumerate(authors,1) if a['equal_contrib'] == 'Y']
    # PubMed's Y flag does not encode groups. Infer co-first only for a single
    # unbroken flagged prefix starting at author 1; mixed senior groups need review.
    unambiguous_equal = len(equal_positions) >= 2 and equal_positions == list(range(1,max(equal_positions)+1))
    if unambiguous_equal:
        first_group.update(equal_positions)
    confirmed = [e for e in matches if e['level']=='confirmed' and e['author_position'] in first_group]
    if confirmed:
        evidence = confirmed[0]
        return 'confirmed', 'first_listed_dceg' if evidence['author_position']==1 else 'structured_cofirst_dceg', matches, sorted(first_group)
    for pos in sorted(first_group):
        a = authors[pos-1]
        if a.get('dceg_branches') and any(e['author_position']==pos and e['kind']=='generic_nci_nih' for e in matches):
            matches.append({'level':'inferred','kind':'official_author_branch_annotation','author_position':pos,
                            'author':a['name'],'text':json.dumps(a['dceg_branches']), 'source':'DCEG bibliography; author-specific annotation'})
            return 'inferred', 'generic_institution_official_author_branch', matches, sorted(first_group)
    if any(e['level']=='confirmed' for e in matches) or any(a.get('dceg_branches') for a in authors):
        reason = 'equal_contribution_group_ambiguous' if equal_positions else 'cofirst_not_reported_in_metadata'
        if first_group and authors[0].get('dceg_branches'):
            reason = 'first_author_affiliation_requires_review'
        return 'unresolved', reason, matches, sorted(first_group)
    if any(e['kind']=='generic_nci_nih' for e in matches):
        return 'unresolved', 'generic_institution_identity_requires_review', matches, sorted(first_group)
    return 'unresolved', 'no_qualifying_affiliation_verified', matches, sorted(first_group)


def merge_records(catalogue, pubmed):
    """PubMed owns author order; augment only uniquely matched authors by identity."""
    r = dict(pubmed)
    r['dceg_id'] = catalogue['dceg_id']
    r['catalogue_ids'] = catalogue.get('catalogue_ids',[catalogue['dceg_id']])
    r['catalogue_url'] = catalogue['url']
    r['source'] = 'PubMed+DCEG'
    r['identifier_aliases'] = catalogue.get('identifier_aliases',[])
    if catalogue['doi'] and pubmed['doi'] and catalogue['doi'] != pubmed['doi']:
        r['identifier_aliases'].append({'type':'doi','value':catalogue['doi'],'source':'DCEG catalogue; differs from canonical PubMed DOI'})
    r['dates'] = pubmed['dates'] + catalogue['dates']
    r['tags'] = pubmed['tags'] + catalogue['tags']
    for field in ['abstract','journal','title','doi']:
        r[field] = pubmed[field] or catalogue[field]
    if norm(r['title']) in {'not available','no title available'}:
        r['title'] = catalogue['title']
    r['authors'] = [dict(a) for a in pubmed['authors']]
    author_index = {}
    for c in catalogue['authors']:
        identity = (norm(c['last_name']),norm(c['first_name']))
        author_index.setdefault(identity,[]).append(c)
    for a in r['authors']:
        matching = author_index.get((norm(a['last_name']),norm(a['first_name'])),[]) if a['last_name'] else []
        if len(matching)==1:
            c = matching[0]
            original_sources = a.get('affiliation_sources',{})
            a['dceg_branches'] = c['dceg_branches']
            a['researcher_id'] = c['researcher_id']
            # Keep both affiliation sources without fabricating author links by order.
            a['affiliations'] = list(dict.fromkeys(a['affiliations'] + c['affiliations']))
            a['affiliation_sources'] = {s:list(dict.fromkeys(
                original_sources.get(s,[]) +
                c.get('affiliation_sources',{}).get(s,[]))) for s in a['affiliations']}
    if not r['authors']:
        r['authors'] = catalogue['authors']
    return r


def join_catalogue_duplicates(primary, secondary):
    """Consolidate the same PMID/DOI while retaining every catalogue source ID."""
    r = dict(primary)
    r['catalogue_ids'] = list(dict.fromkeys(primary.get('catalogue_ids',[primary['dceg_id']]) + secondary.get('catalogue_ids',[secondary['dceg_id']])))
    r['dates'] = primary['dates'] + secondary['dates']
    r['tags'] = primary['tags'] + secondary['tags']
    r['identifier_aliases'] = primary.get('identifier_aliases',[]) + secondary.get('identifier_aliases',[])
    if not r['title'] or norm(r['title']) in {'not available','no title available'}:
        r['title'] = secondary['title']
    return r


SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE publications(
 id TEXT PRIMARY KEY, pmid TEXT, doi TEXT, dceg_id TEXT, title TEXT NOT NULL,
 abstract TEXT, journal TEXT, publication_date TEXT, date_precision TEXT,
 dates_json TEXT, publication_types_json TEXT, status TEXT, reason TEXT,
 date_scope TEXT, first_author_positions_json TEXT, metadata_source TEXT, url TEXT, record_json TEXT);
CREATE UNIQUE INDEX unique_pmid ON publications(pmid) WHERE pmid IS NOT NULL AND pmid!='';
CREATE INDEX publication_status ON publications(status);
CREATE INDEX publication_date ON publications(publication_date);
CREATE TABLE authors(publication_id TEXT REFERENCES publications(id), position INTEGER,
 name TEXT, first_name TEXT, last_name TEXT, orcid TEXT, equal_contrib TEXT,
 dceg_branches_json TEXT, researcher_id TEXT, PRIMARY KEY(publication_id,position));
CREATE TABLE affiliations(publication_id TEXT, author_position INTEGER, text TEXT,
 FOREIGN KEY(publication_id,author_position) REFERENCES authors(publication_id,position));
CREATE TABLE tags(publication_id TEXT REFERENCES publications(id), term TEXT, source TEXT,
 identifier TEXT, major_topic INTEGER, PRIMARY KEY(publication_id,term,source));
CREATE TABLE evidence(publication_id TEXT REFERENCES publications(id), author_position INTEGER,
 kind TEXT, level TEXT, text TEXT, source TEXT);
CREATE TABLE source_records(publication_id TEXT REFERENCES publications(id), source TEXT, source_id TEXT,
 raw_cache TEXT, PRIMARY KEY(publication_id,source,source_id));
CREATE TABLE collection_metadata(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE review_decisions(publication_id TEXT REFERENCES publications(id),decision_json TEXT);
CREATE TABLE identifier_aliases(publication_id TEXT REFERENCES publications(id),type TEXT,value TEXT,source TEXT);
CREATE TABLE inclusion_decisions(publication_id TEXT PRIMARY KEY REFERENCES publications(id),policy_id TEXT,status TEXT,primary_route TEXT,decision_json TEXT);
CREATE TABLE author_membership_decisions(publication_id TEXT REFERENCES publications(id),author_position INTEGER,decision_json TEXT,PRIMARY KEY(publication_id,author_position));
CREATE VIRTUAL TABLE publications_fts USING fts5(publication_id UNINDEXED,title,abstract,authors,tags);
CREATE VIEW confirmed_publications AS SELECT * FROM publications WHERE status='confirmed';
CREATE VIEW review_queue AS SELECT * FROM publications WHERE status IN ('inferred','unresolved');
CREATE VIEW original_first_author_publications AS SELECT * FROM publications WHERE json_extract(record_json,'$.original_first_author_decision.status')='confirmed';
'''


def cached_author_metadata():
    """Reuse cached metadata-only publisher responses; no new network requests."""
    import investigate as investigation
    result={}
    for path in sorted((RAW/'frontmatter').glob('*.xml.gz')):
        root=investigation.strip_namespaces(ET.fromstring(gzip.decompress(path.read_bytes())))
        pmid=next((e.text for e in root.findall('.//article-id') if e.get('pub-id-type')=='pmid'),None)
        if pmid:
            front=investigation.frontmatter(root)
            if front:
                result[pmid]=(front,json.loads(path.with_suffix('.provenance.json').read_text())['url'],path)
    return result


def enrich_cached_authors(record,metadata):
    """Attach publisher affiliations only to uniquely matched named authors."""
    import investigate as investigation
    front,url,path=metadata
    matches={}
    for pos,a in enumerate(front['authors'],1):
        candidates=[i for i,b in enumerate(record['authors'],1) if investigation.compatible(a,b)]
        if len(candidates)==1:matches[pos]=candidates[0]
    # Duplicate publisher entries must not silently map onto the same author.
    unique=Counter(matches.values())
    matches={a:b for a,b in matches.items() if unique[b]==1}
    for pos,mapped in matches.items():
        target=record['authors'][mapped-1]
        for aff in front['authors'][pos-1]['affiliations']:
            if aff not in target['affiliations']:target['affiliations'].append(aff)
            sources=target.setdefault('affiliation_sources',{}).setdefault(aff,[])
            if url not in sources:sources.append(url)
    positions=set(record['first_author_positions'])
    if matches.get(1)==1:
        for group in front['first_groups']:
            if all(pos in matches for pos in group['positions']):
                positions.update(matches[pos] for pos in group['positions'])
    record['inclusion_first_author_positions']=sorted(positions)
    record['publisher_metadata_sources']=[{'url':url,'raw_cache':str(path.relative_to(ROOT)),
                                          'pmcid':path.name.split('.')[0],
                                          'matched_author_positions':sorted(matches.values())}]


def build(args):
    import inclusion
    DB.parent.mkdir(parents=True, exist_ok=True)
    EXPORT.mkdir(exist_ok=True)
    discovery = json.loads((RAW/'discovery.json').read_text())
    if discovery['start'] != args.start or discovery['end'] != args.end:
        raise RuntimeError('Requested dates differ from fetched discovery; run fetch with matching dates')
    identifier_path = ROOT/'config'/'identifier_reviews.json'
    identifier_reviews = json.loads(identifier_path.read_text()) if identifier_path.exists() else {}
    for old,review in identifier_reviews.get('pmid_redirects',{}).items():
        discovery.setdefault('pmid_redirects',{})[old] = review['canonical_pmid']
        discovery['unavailable_pmids'] = [p for p in discovery.get('unavailable_pmids',[]) if p!=old]
    pubmed = {}
    requested_pmids = set(discovery['pmids'])
    print('Loading cached PubMed and catalogue metadata…',flush=True)
    pm_caches = {}
    for path in sorted(list(RAW.glob('pubmed-records-*.xml.gz'))+list(RAW.glob('pubmed-single-*.xml.gz'))):
        root = ET.fromstring(gzip.decompress(path.read_bytes()))
        for el in list(root):
            if el.tag not in {'PubmedArticle','PubmedBookArticle'}:
                continue
            p = from_pubmed(el)
            if p['pmid'] in requested_pmids:
                pubmed[p['pmid']] = p
                pm_caches[p['pmid']] = str(path.relative_to(ROOT))
    missing = set(discovery['pmids']) - set(pubmed) - set(discovery.get('pmid_redirects',{})) - set(discovery.get('unavailable_pmids',[]))
    if missing:
        raise RuntimeError(f'{len(missing)} requested PubMed records not in cache')
    catalogues = {}
    uid_paths = {}
    official_pmids = set()
    catalogue_duplicate_ids = []
    pubmed_by_doi = {}
    for p in pubmed.values():
        if p['doi']:
            pubmed_by_doi.setdefault(p['doi'],[]).append(p['pmid'])
    for path in sorted(RAW.glob('dceg-*.json.gz')):
        for raw in json.loads(gzip.decompress(path.read_bytes()))['publications']:
            p = from_dceg(raw)
            if p['pmid'] in discovery.get('unavailable_pmids',[]) and p['doi'] and len(pubmed_by_doi.get(p['doi'],[]))==1:
                previous = p['pmid']
                p['pmid'] = pubmed_by_doi[p['doi']][0]
                p['identifier_aliases'] = [{'type':'pmid','value':previous,'source':'Unavailable catalogue PMID reconciled to a unique current PubMed record by exact DOI'}]
            if p['pmid'] in discovery.get('pmid_redirects',{}):
                previous = p['pmid']
                p['pmid'] = discovery['pmid_redirects'][previous]
                p['identifier_aliases'] = [{'type':'pmid','value':previous,'source':identifier_reviews.get('pmid_redirects',{}).get(previous,{}).get('note','PubMed canonical citation-page redirect')}]
                if previous in identifier_reviews.get('pmid_redirects',{}) and p['doi']:
                    p['identifier_aliases'].append({'type':'doi','value':p['doi'],'source':identifier_reviews['pmid_redirects'][previous]['note']})
            uid_paths[p['dceg_id']] = str(path.relative_to(ROOT))
            if p['pmid']:
                official_pmids.add(p['pmid'])
            key = 'pmid:'+p['pmid'] if p['pmid'] else 'doi:'+p['doi'] if p['doi'] else 'dceg:'+p['dceg_id']
            if key in catalogues:
                catalogue_duplicate_ids.append(p['dceg_id'])
                catalogues[key] = join_catalogue_duplicates(catalogues[key],p)
            else:
                catalogues[key] = p
    # The catalogue can contain both a PMID-bearing record and a separate
    # DOI-only record for the same paper. Consolidate these before enrichment.
    catalogue_dois = {}
    for k,p in catalogues.items():
        if p['pmid'] and p['doi']:
            catalogue_dois.setdefault(p['doi'],[]).append(k)
    for k,p in list(catalogues.items()):
        targets = catalogue_dois.get(p['doi'],[])
        if not p['pmid'] and p['doi'] and len(targets)==1:
            target = targets[0]
            catalogues[target] = join_catalogue_duplicates(catalogues[target],p)
            catalogue_duplicate_ids.append(p['dceg_id'])
            del catalogues[k]
    records = dict(catalogues)
    doi_keys = {p['doi']:k for k,p in records.items() if p['doi']}
    for pmid,p in pubmed.items():
        key = 'pmid:'+pmid
        if key in records:
            records[key] = merge_records(records[key],p)
        elif p['doi'] and p['doi'] in doi_keys and not records[doi_keys[p['doi']]]['pmid']:
            oldkey = doi_keys[p['doi']]
            records[key] = merge_records(records.pop(oldkey),p)
        else:
            records[key] = p
    tmp = DB.with_suffix('.building.sqlite')
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    counts = {'outside_date_window':0,'catalogue_duplicate_records':len(catalogue_duplicate_ids)}
    inventory = []
    reviews = {}
    for review_path in [ROOT/'config'/'automated_reviews.jsonl',ROOT/'config'/'membership_batch_reviews.jsonl',ROOT/'config'/'reviews.jsonl']:
      if review_path.exists():
        seen_in_file = set()
        for line in review_path.read_text().splitlines():
            if line.strip():
                review = json.loads(line)
                if review['status'] not in {'confirmed','inferred','unresolved','excluded'} or not review.get('note') or not review.get('evidence_url'):
                    raise ValueError('Each manual review requires an allowed status, note, and evidence_url')
                if review['id'] in seen_in_file:
                    raise ValueError('Duplicate manual review for '+review['id'])
                seen_in_file.add(review['id'])
                reviews[review['id']] = review
    publisher_metadata=cached_author_metadata()
    author_membership_reviews={}
    author_review_path=ROOT/'config'/'author_membership_reviews.jsonl'
    if author_review_path.exists():
        for line in author_review_path.read_text().splitlines():
            if not line.strip():continue
            item=json.loads(line)
            if item['status']!='confirmed' or not item.get('note') or not item.get('evidence_url') or item.get('confirmation_basis') not in {'verified_historical_membership','researcher_attestation','published_author_metadata'}:
                raise ValueError('Author membership reviews require sourced confirmed membership evidence')
            key=(item['id'],item['author_position'])
            if key in author_membership_reviews:raise ValueError('Duplicate author membership review: '+str(key))
            author_membership_reviews[key]=item
    print(f'Building database from {len(records):,} reconciled source records…',flush=True)
    for record_index,(key,r) in enumerate(sorted(records.items()),1):
        if record_index % 2000 == 0:
            print(f'Classified {record_index:,}/{len(records):,} source records',flush=True)
        review = reviews.get(key)
        if review and review.get('verified_publication_date'):
            verified_date = date_interval(review['verified_publication_date'])
            if not verified_date or verified_date['precision']!='day':
                raise ValueError('Verified publication date requires a valid full date: '+key)
            verified_date.update({'kind':'verified_first_publication','source':review['evidence_url']})
            r['dates'].append(verified_date)
            r['verified_publication_date'] = verified_date
        scope = in_scope(r,args.start,args.end)
        inventory.append({'id':key,'pmid':r['pmid'],'dceg_id':r['dceg_id'],'title':r['title'],'date_scope':scope})
        if scope == 'outside':
            counts['outside_date_window'] += 1
            continue
        status,reason,evidence,first_positions = evaluate(r)
        affiliation_status, affiliation_reason = status, reason
        r['is_preprint'] = any('preprint' in norm(t) for t in r['publication_types'])
        r['affiliation_decision'] = {'status':affiliation_status,'reason':affiliation_reason}
        if r['is_preprint']:
            status,reason = 'excluded','preprint_kept_separate'
        if r['pmid'] in discovery.get('unavailable_pmids',[]):
            status,reason = 'unresolved','pubmed_identifier_requires_reconciliation'
            r['source_issues'] = ['Requested PMID returned no citation XML; retain catalogue metadata and check replacement identifiers.']
        if scope != 'inside':
            status,reason = 'unresolved','publication_date_'+scope
        automatic_status, automatic_reason = status, reason
        if review:
            positions = review.get('qualifying_author_positions',[])
            if review['status']=='confirmed' and (r['is_preprint'] or scope!='inside' or not positions or any(not isinstance(i,int) or i<1 or i>len(r['authors']) for i in positions)):
                raise ValueError('A confirmed review requires valid qualifying author positions and a resolved in-window date: '+key)
            status,reason = review['status'],'manual_review'
            for pos in positions or review.get('author_positions_reviewed',[None]):
                evidence.append({'level':status,'kind':review.get('evidence_kind','manual_review'),'author_position':pos,
                                 'author':r['authors'][pos-1]['name'] if pos else '',
                                 'text':review['note'],'source':review['evidence_url']})
            for pos,affiliations in review.get('verified_affiliations',{}).items():
                a=r['authors'][int(pos)-1]
                for affiliation in affiliations:
                    if affiliation not in a['affiliations']:a['affiliations'].append(affiliation)
                    a.setdefault('affiliation_sources',{}).setdefault(affiliation,[]).append(review.get('verified_affiliation_source','PMC front matter'))
            first_positions = sorted(set(first_positions + positions))
        status,reason=apply_publication_scope(r,status,reason)
        if status=='confirmed':
            r['confirmation_basis'] = review.get('confirmation_basis','published_author_metadata') if review else 'published_affiliation'
        earliest = r.get('verified_publication_date') or (min(r['dates'],key=lambda d:d['lower']) if r['dates'] else {})
        r.update({'id':key,'status':status,'reason':reason,'date_scope':scope,
                  'first_author_positions':first_positions,'evidence':evidence,
                  'publication_date':earliest.get('value'),'date_precision':earliest.get('precision')})
        r['automatic_decision'] = {'status':automatic_status,'reason':automatic_reason}
        if review:
            r['manual_review'] = review
        # Preserve the previous first/co-first policy before adding publisher
        # metadata and applying the broader independently evaluated policy.
        import copy
        original={k:copy.deepcopy(r.get(k)) for k in ['status','reason','confirmation_basis','first_author_positions','automatic_decision','evidence']}
        if r['pmid'] in publisher_metadata:
            enrich_cached_authors(r,publisher_metadata[r['pmid']])
        r['author_membership_reviews']=[author_membership_reviews[(key,pos)] for pos in range(1,len(r['authors'])+1) if (key,pos) in author_membership_reviews]
        inclusion.apply(r,affiliation_evidence)
        r['original_first_author_decision']=original
        status,reason=r['status'],r['reason']
        if r['is_preprint']:
            r['eligibility_before_publication_scope']={'status':status,'reason':reason}
            status,reason='excluded','preprint_kept_separate'
        status,reason=apply_publication_scope(r,status,reason)
        r['status'],r['reason']=status,reason
        evidence=r['evidence']
        if status!='confirmed':r.pop('confirmation_basis',None)
        db.execute('INSERT INTO publications VALUES('+','.join('?' for _ in range(18))+')',(
            key,r['pmid'] or None,r['doi'] or None,r['dceg_id'],r['title'],r['abstract'] or None,r['journal'],
            r['publication_date'],r['date_precision'],json.dumps(r['dates']),json.dumps(r['publication_types']),
            status,reason,scope,json.dumps(first_positions),r['source'],r['url'],json.dumps(r,ensure_ascii=False)))
        decision=r['inclusion_decision']
        db.execute('INSERT INTO inclusion_decisions VALUES(?,?,?,?,?)',
                   (key,decision['policy_id'],decision['status'],decision['primary_route'],json.dumps(decision,ensure_ascii=False)))
        for item in r['author_membership_reviews']:
            db.execute('INSERT INTO author_membership_decisions VALUES(?,?,?)',(key,item['author_position'],json.dumps(item,ensure_ascii=False)))
        for i,a in enumerate(r['authors'],1):
            db.execute('INSERT INTO authors VALUES(?,?,?,?,?,?,?,?,?)',(key,i,a['name'],a['first_name'],a['last_name'],a['orcid'],a['equal_contrib'],json.dumps(a['dceg_branches']),a['researcher_id']))
            for aff in a['affiliations']:
                db.execute('INSERT INTO affiliations VALUES(?,?,?)',(key,i,aff))
        for t in r['tags']:
            if t['term']:
                db.execute('INSERT OR IGNORE INTO tags VALUES(?,?,?,?,?)',(key,t['term'],t['source'],t.get('identifier'),int(t.get('major_topic',False))))
        for e in evidence:
            db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?)',(key,e['author_position'],e['kind'],e['level'],e['text'],e['source']))
        if review:
            db.execute('INSERT INTO review_decisions VALUES(?,?)',(key,json.dumps(review)))
        for alias in r.get('identifier_aliases',[]):
            db.execute('INSERT INTO identifier_aliases VALUES(?,?,?,?)',(key,alias['type'],alias['value'],alias['source']))
        if r['pmid'] in pm_caches:
            db.execute('INSERT INTO source_records VALUES(?,?,?,?)',(key,'PubMed',r['pmid'],pm_caches[r['pmid']]))
        if r['dceg_id']:
            for catalogue_id in r.get('catalogue_ids',[r['dceg_id']]):
                db.execute('INSERT INTO source_records VALUES(?,?,?,?)',(key,'DCEG',catalogue_id,uid_paths[catalogue_id]))
        if review and review.get('pmcid'):
            db.execute('INSERT INTO source_records VALUES(?,?,?,?)',(key,'PMC front matter',review['pmcid'],
                'data/raw/frontmatter/'+review['pmcid']+'.xml.gz'))
        for source in r.get('publisher_metadata_sources',[]):
            db.execute('INSERT OR IGNORE INTO source_records VALUES(?,?,?,?)',
                       (key,'PMC front matter',source['pmcid'],source['raw_cache']))
        db.execute('INSERT INTO publications_fts VALUES(?,?,?,?,?)',(key,r['title'],r['abstract'],'; '.join(a['name'] for a in r['authors']),'; '.join(t['term'] for t in r['tags'])))
    metadata = {'built_at':now(),'window_start':args.start,'window_end':args.end,
                'definition':inclusion.POLICY['definition'],'inclusion_policy':inclusion.POLICY,
                'discovery':discovery,'audit_counts':counts}
    for k,v in metadata.items():
        db.execute('INSERT INTO collection_metadata VALUES(?,?)',(k,json.dumps(v)))
    db.commit()
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    db.close()
    tmp.replace(DB)
    write_csv(EXPORT/'source_inventory.csv',inventory)
    export(args)


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        path.write_text('')
        return
    with path.open('w',newline='',encoding='utf-8') as f:
        writer = csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            # Avoid spreadsheet formula execution when opening downloaded metadata.
            safe = {k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in row.items()}
            writer.writerow(safe)


def export(args):
    import inclusion
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    records = [json.loads(p[0]) for p in db.execute('SELECT record_json FROM publications ORDER BY publication_date DESC,title')]
    for label, selected in [('confirmed',[r for r in records if r['status']=='confirmed']),('candidates',records),
                            ('original_first_author',[r for r in records if r.get('original_first_author_decision',{}).get('status')=='confirmed']),
                            ('preprints',[r for r in records if r.get('is_preprint')]),
                            ('conference_abstracts',[r for r in records if r.get('is_conference_abstract')]),
                            ('review_queue',[r for r in records if r['status'] in {'inferred','unresolved'}])]:
        priority = {'pubmed_identifier_requires_reconciliation':1,'first_author_affiliation_requires_review':1,
                    'generic_institution_official_author_branch':1,'equal_contribution_group_ambiguous':1,
                    'cofirst_not_reported_in_metadata':2,'generic_institution_identity_requires_review':3}
        if label=='review_queue':
            selected = sorted(selected,key=lambda r:(priority.get(r['reason'],4),r['publication_date'] or '',r['title']))
        with (EXPORT/(label+'.jsonl')).open('w') as f:
            for r in selected:
                f.write(json.dumps(r,ensure_ascii=False)+'\n')
        rows = []
        for r in selected:
            rows.append({k:r.get(k) for k in ['id','pmid','doi','dceg_id','title','abstract','journal','publication_date','date_precision','status','reason','url']} | {
                'authors':'; '.join(a['name'] for a in r['authors']),
                'publication_types':'; '.join(r['publication_types']),
                'eligibility_status':r.get('eligibility_before_publication_scope',{}).get('status',r.get('affiliation_decision',{}).get('status',r['status']) if r.get('is_preprint') else r['status']),
                'automatic_affiliation_status':r.get('affiliation_decision',{}).get('status',''),
                'tags':'; '.join(sorted(set(t['term'] for t in r['tags']))),
                'review_priority':priority.get(r['reason'],4) if r['status'] in {'inferred','unresolved'} else '',
                'evidence_json':json.dumps(r['evidence'],ensure_ascii=False),
                'review_status':r.get('manual_review',{}).get('status',''),
                'review_note':r.get('manual_review',{}).get('note',''),
                'review_evidence_url':r.get('manual_review',{}).get('evidence_url',''),
                'confirmation_basis':r.get('confirmation_basis',''),
                'inclusion_routes':'; '.join(r.get('inclusion_routes',[])),
                'qualifying_author_positions':json.dumps(r.get('qualifying_author_positions',[])),
                'verified_dceg_author_positions':json.dumps(r.get('inclusion_decision',{}).get('verified_dceg_author_positions',[])),
                'first_author_nih_affiliation_verified':r.get('inclusion_decision',{}).get('first_author_nih_affiliation_verified',False),
                'original_first_author_status':r.get('original_first_author_decision',{}).get('status',''),
                'original_first_author_reason':r.get('original_first_author_decision',{}).get('reason',''),
                'original_first_author_evidence_json':json.dumps(r.get('original_first_author_decision',{}).get('evidence',[]),ensure_ascii=False)})
        write_csv(EXPORT/(label+'.csv'),rows)
    status = dict(db.execute('SELECT status,count(*) FROM publications GROUP BY status'))
    by_year = [dict(row) for row in db.execute("SELECT substr(publication_date,1,4) year,status,count(*) count FROM publications GROUP BY year,status ORDER BY year,status")]
    by_reason = dict(db.execute('SELECT reason,count(*) FROM publications GROUP BY reason'))
    missing = {field:db.execute(f"SELECT count(*) FROM publications WHERE status='confirmed' AND ({field} IS NULL OR {field}='')").fetchone()[0] for field in ['abstract','journal','doi','publication_date']}
    tag_missing = db.execute("SELECT count(*) FROM publications p WHERE status='confirmed' AND NOT EXISTS(SELECT 1 FROM tags t WHERE t.publication_id=p.id)").fetchone()[0]
    new_confirmed = db.execute("SELECT count(*) FROM publications WHERE status='confirmed' AND dceg_id IS NULL").fetchone()[0]
    summary = {'built_at':now(),'start':args.start,'end':args.end,'counts':status,'reasons':by_reason,
               'inclusion_policy':inclusion.POLICY,
               'primary_inclusion_routes':dict(Counter(r['inclusion_decision']['primary_route'] for r in records if r['status']=='confirmed')),
               'original_first_author_confirmed':sum(r.get('original_first_author_decision',{}).get('status')=='confirmed' for r in records),
               'confirmation_bases':dict(Counter(r.get('confirmation_basis','unspecified') for r in records if r['status']=='confirmed')),
               'confirmed_missing_fields':missing,'confirmed_without_tags':tag_missing,
               'confirmed_not_in_dceg_catalogue':new_confirmed,'by_year':by_year,
               'preprints_kept_separate':sum(bool(r.get('is_preprint')) for r in records),
               'conference_abstracts_kept_separate':sum(r['reason']=='conference_abstract_kept_separate' for r in records),
               'confirmed_conference_abstracts':sum(r.get('eligibility_before_publication_scope',{}).get('status')=='confirmed' for r in records if r.get('is_conference_abstract')),
               'complete':False,
               'remaining_gaps':[
                   'Generic, missing and external-only affiliations need dated DCEG membership evidence for first, last or other qualifying authors. Names and branch annotations alone are not proof.',
                   'The NIH-first fallback requires a first-listed author affiliation on the paper; NIH funding and email addresses alone do not establish it.',
                   'Documented co-first authors qualify; equal-senior groups are not automatically treated as last-listed authors.',
                   'Researcher-name expansion was seeded for first-author discovery. Generic-affiliation last-author and coauthor papers missing from the catalogue may still be missed.',
                   'Non-PubMed papers absent from the DCEG catalogue are not yet independently searched.',
                   'Historical branch aliases and organizational changes need further source-backed reconciliation.',
                   'Publication dates that straddle a window boundary or are absent remain unresolved.',
                   'Corrections, editorials, letters, and other publication types are retained and labelled, pending any narrower scope preference.'
               ]}
    saved_discovery = db.execute("SELECT value FROM collection_metadata WHERE key='discovery'").fetchone()
    discovery = json.loads(saved_discovery[0]) if saved_discovery else json.loads((RAW/'discovery.json').read_text())
    summary['source_counts'] = {'dceg_catalogue_records':discovery['dceg_inventory_count'],
                                'pubmed_requested':len(discovery['pmids']),
                                'researcher_name_seeds':len(discovery['seed_names'])}
    summary['pubmed_unavailable_identifiers'] = discovery.get('unavailable_pmids',[])
    summary['pubmed_redirects'] = discovery.get('pmid_redirects',{})
    changes=[{'id':r['id'],'title':r['title'],'previous_status':r.get('original_first_author_decision',{}).get('status'),
              'current_status':r['status'],'inclusion_routes':'; '.join(r.get('inclusion_routes',[])),
              'qualifying_author_positions':json.dumps(r.get('qualifying_author_positions',[])),
              'publication_date':r['publication_date'],'url':r['url'],
              'evidence_json':json.dumps(r['evidence'],ensure_ascii=False)} for r in records
             if r.get('original_first_author_decision',{}).get('status')!=r['status']]
    write_csv(EXPORT/'inclusion_policy_changes.csv',changes)
    (EXPORT/'inclusion_policy_changes.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in changes))
    policy_summary={'policy':inclusion.POLICY,'source_window':{'start':args.start,'end':args.end},
                    'current_counts':status,'original_first_author_confirmed':summary['original_first_author_confirmed'],
                    'primary_inclusion_routes':summary['primary_inclusion_routes'],
                    'newly_confirmed':sum(r['current_status']=='confirmed' and r['previous_status']!='confirmed' for r in changes),
                    'transitions':dict(Counter(r['previous_status']+' -> '+r['current_status'] for r in changes)),
                    'complete':False}
    (EXPORT/'inclusion_policy_summary.json').write_text(json.dumps(policy_summary,indent=2)+'\n')
    (EXPORT/'coverage.json').write_text(json.dumps(summary,indent=2)+'\n')
    # A self-contained local browser for the database, with no external requests.
    lite = [{k:r.get(k) for k in ['id','title','abstract','journal','publication_date','status','reason','url','doi','confirmation_basis','inclusion_routes','original_first_author_decision']} | {
        'publication_types':r['publication_types'], 'is_preprint':r.get('is_preprint',False),
        'authors':[a['name'] for a in r['authors']], 'tags':sorted(set(t['term'] for t in r['tags'])),
        'evidence':r['evidence']} for r in records]
    template = (ROOT/'scripts'/'browser.template.html').read_text()
    embedded = json.dumps(lite,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    (EXPORT/'browse.html').write_text(template.replace('__DATABASE__',embedded).replace('__SUMMARY__',json.dumps(summary).replace('<','\\u003c')))
    db.close()
    print(json.dumps(summary,indent=2),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['fetch','build','export','all'])
    parser.add_argument('--start',default='2020-01-01')
    parser.add_argument('--end',default=date.today().isoformat())
    args = parser.parse_args()
    date.fromisoformat(args.start);date.fromisoformat(args.end)
    if args.start > args.end:
        parser.error('--start must not follow --end')
    if args.command in {'fetch','all'}:
        fetch(args)
    if args.command in {'build','all'}:
        build(args)
    if args.command == 'export':
        export(args)


if __name__ == '__main__':
    main()
