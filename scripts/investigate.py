#!/usr/bin/env python3
"""Rank unresolved identities and verify publication author metadata against PMC."""
import argparse
from collections import Counter,defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
import collect as c

OUT=c.ROOT/'exports'
CACHE=c.RAW/'frontmatter'


def identity(a):
    return (c.norm(a.get('last_name')),c.norm(a.get('first_name')))


def orcid(a):
    return (a.get('orcid') or '').rstrip('/').split('/')[-1]


def compatible(a,b):
    if orcid(a) and orcid(b):
        return orcid(a)==orcid(b)
    al,af=identity(a);bl,bf=identity(b)
    if not al or al!=bl or not af or not bf:
        return False
    af,bf=af.split()[0],bf.split()[0]
    return af==bf or (min(len(af),len(bf))==1 and af[0]==bf[0])


STOP=set('the and of in a to for with on by from an as is are at among using study analysis risk humans human female male adult aged middle neoplasms cancer'.split())


def terms(r):
    text=r['title']+' '+' '.join(t['term'] for t in r['tags'])
    return Counter(w for w in c.norm(text).split() if len(w)>2 and w not in STOP and not w.isdigit())


def load():
    db=sqlite3.connect(c.DB)
    rows=[json.loads(row[0]) for row in db.execute('SELECT record_json FROM publications')]
    db.close()
    return rows


def rank():
    rows=load()
    profiles={};by_initial=defaultdict(set);by_orcid=defaultdict(set)
    for r in rows:
        if r.get('is_preprint'):
            continue
        for a in r['authors']:
            if not any((c.affiliation_evidence(s) or {}).get('level')=='confirmed' for s in a['affiliations']):
                continue
            key=orcid(a) or '|'.join(identity(a))
            if not key or not a['last_name']:
                continue
            p=profiles.setdefault(key,{'author':a,'papers':{},'coauthors':Counter(),'terms':Counter(),'years':set()})
            if r['id'] not in p['papers']:
                p['papers'][r['id']]=r['url']
                p['coauthors'].update(set(identity(b) for b in r['authors'] if identity(b)!=identity(a)))
                p['terms'].update(terms(r))
                p['years'].add((r.get('publication_date') or '')[:4])
            if len(c.norm(a['first_name']))>len(c.norm(p['author']['first_name'])):
                p['author']=a
            ln,fn=identity(a)
            if ln and fn:by_initial[(ln,fn[0])].add(key)
            if orcid(a):by_orcid[orcid(a)].add(key)
    ranked=[]
    for r in rows:
        if r['status'] not in {'unresolved','inferred'} or r.get('is_preprint') or r['date_scope']!='inside':
            continue
        fresh,reason,_,_=c.evaluate(r)
        if fresh=='confirmed':
            continue
        first=r['authors'][0] if r['authors'] else None
        best=None
        if first:
            ln,fn=identity(first)
            keys=set(by_orcid.get(orcid(first),set()))|set(by_initial.get((ln,fn[:1]),set()))
            for key in keys:
                p=profiles[key]
                if not compatible(first,p['author']):
                    continue
                coauthors=set(identity(b) for b in r['authors'][1:])
                shared=sorted(coauthors & set(p['coauthors']))
                rt,pt=terms(r),p['terms']
                common=set(rt)&set(pt)
                cosine=sum(rt[w]*pt[w] for w in common)/(math.sqrt(sum(v*v for v in rt.values()))*math.sqrt(sum(v*v for v in pt.values())) or 1)
                same_orcid=bool(orcid(first) and orcid(first)==orcid(p['author']))
                same_name=identity(first)==identity(p['author'])
                score=(100 if same_orcid else 0)+(20 if same_name else 3)+6*min(8,len(shared))+30*cosine
                if (r.get('publication_date') or '')[:4] in p['years']:score+=5
                candidate={'score':round(score,2),'matched_researcher':p['author']['name'],
                           'orcid_match':same_orcid,'exact_name_match':same_name,
                           'shared_coauthors':[' '.join(reversed(x)) for x in shared[:15]],
                           'shared_topic_terms':sorted(common,key=lambda w:rt[w]*pt[w],reverse=True)[:15],
                           'topic_cosine':round(cosine,3),'reference_publications':list(p['papers'].values())[:8]}
                if best is None or score>best['score']:best=candidate
        dceg_later=[]
        for pos,a in enumerate(r['authors'][1:],2):
            if any((c.affiliation_evidence(s) or {}).get('level')=='confirmed' for s in a['affiliations']):
                dceg_later.append({'position':pos,'name':a['name'],'equal_contrib':a['equal_contrib']})
        early=[a for a in dceg_later if a['position']<=10]
        cofirst_score=max((100/(a['position']-0.5)+(35 if a['equal_contrib']=='Y' else 0) for a in early),default=0)
        if reason=='equal_contribution_group_ambiguous' and early:cofirst_score+=15
        score=max((best or {}).get('score',0),cofirst_score)
        ranked.append({'id':r['id'],'pmid':r['pmid'],'title':r['title'],'reason':r['reason'],
                       'first_author':first['name'] if first else '', 'score':round(score,2),
                       'identity_assessment':best,'dceg_later_authors':dceg_later,
                       'method':'Coauthor/topic/name/ORCID signals prioritize investigation; they do not establish affiliation or first authorship.'})
    ranked.sort(key=lambda r:(-r['score'],r['id']))
    (OUT/'investigation_candidates.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in ranked))
    c.write_csv(OUT/'investigation_candidates.csv',[{
        'id':r['id'],'score':r['score'],'title':r['title'],'first_author':r['first_author'],'reason':r['reason'],
        'matched_researcher':(r['identity_assessment'] or {}).get('matched_researcher',''),
        'shared_coauthors':'; '.join((r['identity_assessment'] or {}).get('shared_coauthors',[])),
        'shared_topics':'; '.join((r['identity_assessment'] or {}).get('shared_topic_terms',[])),
        'orcid_match':(r['identity_assessment'] or {}).get('orcid_match',False)} for r in ranked])
    print(f'Ranked {len(ranked):,} candidates using {len(profiles):,} DCEG identity profiles.',flush=True)
    for r in ranked[:15]:print(r['id'],r['score'],r['first_author'],r['title'][:80],flush=True)
    return ranked


def pmcid_map():
    result={}
    for path in sorted(c.RAW.glob('pubmed-*.xml.gz')):
        root=ET.fromstring(gzip.decompress(path.read_bytes()))
        for p in root.findall('PubmedArticle'):
            pmid=p.findtext('./MedlineCitation/PMID')
            pmc=next((a.text for a in p.findall('./PubmedData/ArticleIdList/ArticleId') if a.get('IdType')=='pmc'),None)
            if pmid and pmc:result[pmid]=pmc
    return result


def strip_namespaces(root):
    for e in root.iter():e.tag=e.tag.split('}')[-1]
    return root


def frontmatter(root):
    root=strip_namespaces(root)
    article=root.find('.//article')
    if article is None:
        return None
    front=article.find('front')
    if front is None:return None
    affs={e.get('id'):c.plain(' '.join(e.itertext())) for e in front.findall('.//aff') if e.get('id')}
    # Some publishers print "1 Clinical Genetics Branch and 2 ... , Division
    # of ..." with a shared institutional tail. Reconstruct only explicit
    # continuation syntax, never borrow an arbitrary coauthor's institution.
    aff_elements=[e for e in front.findall('.//aff') if e.get('id')]
    reconstructions={}
    for ix,e in enumerate(aff_elements[:-1]):
        text=affs[e.get('id')]
        is_branch=any(c.norm(b['name']) in c.norm(text) for b in c.CONFIG['branches'])
        if not is_branch or not re.search(r'(?:\band|,)\s*$',text,re.I):continue
        chain=[text]
        for later in aff_elements[ix+1:ix+5]:
            following=affs[later.get('id')];chain.append(following)
            tail=re.search(r'Division of Cancer Epidemiology(?: and| &) Genetics.*',following,re.I)
            if tail:
                affs[e.get('id')]=re.sub(r'[,\s]*(?:and)?\s*$','',text,flags=re.I)+', '+tail.group()
                reconstructions[e.get('id')]={'original':text,'shared_tail_source':chain,'resolved':affs[e.get('id')]}
                break
            if not (any(c.norm(b['name']) in c.norm(following) for b in c.CONFIG['branches']) and re.search(r'(?:\band|,)\s*$',following,re.I)):break
    notes={e.get('id'):c.plain(''.join(e.itertext())) for e in front.findall('.//author-notes/*') if e.get('id')}
    # Publisher equal-contribution notes can also be children of contrib-group.
    for e in front.findall('.//fn'):
        if e.get('id'):notes[e.get('id')]=c.plain(''.join(e.itertext()))
    correspondence={e.get('id') or '_correspondence_'+str(ix):c.plain(' '.join(e.itertext()))
                    for ix,e in enumerate(front.findall('.//author-notes/corresp'))}
    notes.update(correspondence)
    authors=[]
    for a in front.findall('./article-meta/contrib-group/contrib'):
        if a.get('contrib-type') not in {None,'author'}:continue
        name=a.find('name')
        if name is None:name=a.find('string-name')
        if name is None:
            name=a.find('name-alternatives/name')
        if name is None:continue
        given=c.text_at(name,'given-names');last=c.text_at(name,'surname')
        ids=[rid for x in a.findall('xref') for rid in (x.get('rid') or '').split()]
        correspondence_ids=[rid for x in a.findall('xref') if x.get('ref-type')=='corresp' for rid in (x.get('rid') or '').split()]
        affiliations=[affs[i] for i in ids if i in affs]+[c.plain(''.join(e.itertext())) for e in a.findall('aff')]
        authors.append({'name':(given+' '+last).strip(),'first_name':given,'last_name':last,
                        'orcid':next((c.plain(e.text) for e in a.findall('contrib-id') if e.get('contrib-id-type')=='orcid'),''),
                        'affiliations':list(dict.fromkeys(affiliations)),
                        'note_ids':[i for i in ids if i in notes], 'correspondence_ids':correspondence_ids,
                        'equal_contrib':a.get('equal-contrib')})
    for a in authors:
        for note_id,note in correspondence.items():
            named=[b for b in authors if c.norm(b['name']) and c.norm(b['name']) in c.norm(note)]
            if len(named)==1 and named[0] is a and note.count('@')<=1 and note_id not in a['correspondence_ids']:
                a['correspondence_ids'].append(note_id)
        for note_id in a['correspondence_ids']:
            note=notes.get(note_id,'')
            linked=sum(note_id in b['correspondence_ids'] for b in authors)
            if linked==1 and c.norm(a['last_name']) in c.norm(note).split() and (c.affiliation_evidence(note) or {}).get('level')=='confirmed':
                a['affiliations'].append(note)
    # Some journals put an author's current address in an unlinked note rather
    # than in the affiliation list. Require one explicitly named author.
    for note in notes.values():
        n=c.norm(note)
        named=[a for a in authors if c.norm(a['name']) and (' '+c.norm(a['name'])+' ') in (' '+n+' ')]
        if len(named)==1 and re.search(r'now at|current address|present address',n) and (c.affiliation_evidence(note) or {}).get('level')=='confirmed':
            named[0]['affiliations'].append(note)
    groups=[]
    for note_id,note in notes.items():
        n=c.norm(note)
        if re.search(r'contribut\w* equal|equal(?:ly)? contribut|joint first|co ?first',n) and not re.search(r'jointly supervised|co ?senior|senior authors',n):
            positions=[i for i,a in enumerate(authors,1) if note_id in a['note_ids']]
            if not positions:
                # Full names can identify an unlinked first-author note. Initials
                # alone remain ambiguous, especially in large author lists.
                positions=[i for i,a in enumerate(authors,1) if c.norm(a['name']) and (' '+c.norm(a['name'])+' ') in (' '+n+' ')]
            if 1 in positions and len(positions)>1:groups.append({'positions':positions,'note':note,'note_id':note_id})
    return {'authors':authors,'author_notes':notes,'first_groups':groups,
            'affiliation_reconstructions':reconstructions,
            'title':c.text_at(front,'article-meta/title-group/article-title')}


def decide(r,f,url):
    if not f or not f['authors'] or not r['authors']:return None
    # Match publication identity and each qualifying author explicitly; author
    # positions may differ when consortium names are inserted in one source.
    if not compatible(r['authors'][0],f['authors'][0]):return None
    positions={1}
    for group in f['first_groups']:positions.update(group['positions'])
    matched=[];quotes=[];verified={}
    for pos in sorted(positions):
        a=f['authors'][pos-1]
        qualifying=[s for s in a['affiliations'] if (c.affiliation_evidence(s) or {}).get('level')=='confirmed']
        if not qualifying:continue
        candidates=[i for i,b in enumerate(r['authors'],1) if compatible(a,b)]
        if len(candidates)!=1:continue
        matched.append(candidates[0]);quotes.append(a['name']+': '+qualifying[0]);verified[str(candidates[0])]=qualifying
    if matched:
        group_text='; '.join(g['note'] for g in f['first_groups'])
        return {'id':r['id'],'status':'confirmed','qualifying_author_positions':matched,
                'note':'PMC publisher front matter verifies qualifying affiliation. '+'; '.join(quotes)+
                       (' Joint first-author note: '+group_text if group_text else ' First-listed author qualifies.'),
                'evidence_url':url,'reviewed_on':'2026-10-15','method':'pmc_front_matter',
                'source_author_notes':f['author_notes'],'verified_affiliations':verified}
    # A publisher note explicitly establishes a first-author group whose stated
    # affiliations all lie outside NCI/NIH/DCEG; later DCEG authors do not qualify.
    if f['first_groups'] and all(f['authors'][p-1]['affiliations'] and
        all(re.search(r'university|hospital|research center|research centre|medical center|medical centre',s,re.I) for s in f['authors'][p-1]['affiliations']) and
        not any(c.affiliation_evidence(s) for s in f['authors'][p-1]['affiliations']) for p in positions):
        return {'id':r['id'],'status':'excluded','qualifying_author_positions':[],
                'note':'Publisher front matter explicitly identifies the first-author group ('+', '.join(f['authors'][p-1]['name'] for p in sorted(positions))+'); its stated affiliations are outside DCEG/NCI/NIH. DCEG authors appear outside that group. Notes: '+'; '.join(g['note'] for g in f['first_groups']),
                'evidence_url':url,'reviewed_on':'2026-10-15','method':'pmc_front_matter',
                'source_author_notes':f['author_notes']}
    return None


def resolve_cached():
    rows={r['pmid']:r for r in load() if r['pmid']};reviews=[];audit=[]
    for path in sorted(CACHE.glob('*.xml.gz')):
        root=strip_namespaces(ET.fromstring(gzip.decompress(path.read_bytes())))
        pmid=next((a.text for a in root.findall('.//article-id') if a.get('pub-id-type')=='pmid'),None)
        if pmid not in rows:continue
        r=rows[pmid];f=frontmatter(root)
        provenance=json.loads(path.with_suffix('.provenance.json').read_text())
        review=decide(r,f,provenance['url'])
        if review:
            review['pmcid']=path.name.split('.')[0];reviews.append(review)
        audit.append({'id':r['id'],'decision':review,'front_matter':f,'source_url':provenance['url']})
    (c.ROOT/'config'/'automated_reviews.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in sorted(reviews,key=lambda r:r['id'])))
    (OUT/'frontmatter_investigation.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in audit))
    print(json.dumps({'cached_front_matter_checked':len(audit),'decisions':dict(Counter(r['status'] for r in reviews))},indent=2))


def corroborate_identities():
    rows=load();references=defaultdict(list)
    for r in rows:
        if r.get('is_preprint'):continue
        for a in r['authors']:
            if orcid(a) and any((c.affiliation_evidence(s) or {}).get('level')=='confirmed' for s in a['affiliations']):
                references[orcid(a)].append((r,a))
    path=c.ROOT/'config'/'automated_reviews.jsonl'
    existing={r['id']:r for r in map(json.loads,path.read_text().splitlines())} if path.exists() else {}
    ranked={r['id']:r for r in map(json.loads,(OUT/'investigation_candidates.jsonl').read_text().splitlines())}
    found=[]
    for r in rows:
        if r['status']!='unresolved' or r['id'] in existing or r.get('is_preprint') or r['date_scope']!='inside' or not r['authors']:continue
        a=r['authors'][0];assessment=(ranked.get(r['id'],{}).get('identity_assessment') or {})
        generic=[s for s in a['affiliations'] if (c.affiliation_evidence(s) or {}).get('kind')=='generic_nci_nih']
        other_unit=re.compile(r'heart|lung and blood institute|drug abuse|environmental health sciences|neurological|center for cancer research|division of cancer (?:prevention|control|treatment)|national library',re.I)
        if not generic or any(other_unit.search(s) for s in generic) or not assessment.get('orcid_match') or len(assessment.get('shared_coauthors',[]))<3 or assessment.get('topic_cosine',0)<0.1:continue
        year=int(r['publication_date'][:4]);support=[];seen=set()
        for ref,b in references[orcid(a)]:
            if ref['id']==r['id'] or ref['id'] in seen or not ref.get('publication_date') or abs(int(ref['publication_date'][:4])-year)>1:continue
            seen.add(ref['id']);support.append({'publication':ref['url'],'year':ref['publication_date'][:4],
                'reported_affiliation':next(s for s in b['affiliations'] if (c.affiliation_evidence(s) or {}).get('level')=='confirmed')})
        if len(support)<2:continue
        review={'id':r['id'],'status':'inferred','qualifying_author_positions':[1],
            'note':'Identity corroborated by exact ORCID '+orcid(a)+', '+str(len(assessment['shared_coauthors']))+' shared coauthors and overlapping topics ('+', '.join(assessment['shared_topic_terms'][:6])+'). The paper reports only a broader NCI/NIH affiliation. At least two independent publications within one year report this author at DCEG. Identity is corroborated; DCEG affiliation on this specific paper remains inferred.',
            'evidence_url':r['url'],'reviewed_on':'2026-10-15','method':'orcid_coauthor_topic_corroboration',
            'identity_assessment':assessment,'contemporaneous_references':support[:6],
            'reported_generic_affiliation':generic,'orcid':orcid(a)}
        existing[r['id']]=review;found.append(review)
    path.write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n' for k,v in sorted(existing.items())))
    (OUT/'identity_corroborations.jsonl').write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in found))
    print(f'Corroborated {len(found)} identities; retained as inferred affiliations, not confirmed.',flush=True)


def report():
    rows=load();baseline=json.loads((OUT/'review_baseline.json').read_text())
    current=Counter(r['status'] for r in rows);changes=[]
    for r in rows:
        before=baseline['records'].get(r['id'],{'status':'excluded' if r.get('is_preprint') else 'confirmed'})
        if before and before['status']!=r['status']:
            review=r.get('manual_review',{})
            changes.append({'id':r['id'],'title':r['title'],'previous_status':before['status'],
                'current_status':r['status'],'method':'conference_abstract_scope_separation' if r['reason']=='conference_abstract_kept_separate' else review.get('method','normalized_published_affiliation'),
                'evidence_url':review.get('evidence_url',r['url']),
                'note':review.get('note','Reported affiliation recognized after bibliographic abbreviation normalization.')})
    c.write_csv(OUT/'resolution_changes.csv',changes)
    summary={'before':baseline['counts'],'after':dict(current),'changed_statuses':len(changes),
             'transitions':dict(Counter(x['previous_status']+' → '+x['current_status'] for x in changes)),
             'publisher_metadata_records_checked':len(list(CACHE.glob('*.xml.gz'))),
             'method_counts':dict(Counter(x['method'] for x in changes)),
             'new_confirmed_nonabstract_publications':sum(x['current_status']=='confirmed' for x in changes),
             'new_qualifying_conference_abstracts':sum(r.get('eligibility_before_publication_scope',{}).get('status')=='confirmed' and r['id'] in baseline['records'] for r in rows if r.get('is_conference_abstract')),
             'not_complete':True}
    (OUT/'resolution_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


def investigate(limit):
    if not 1<=limit<=90:raise ValueError('Limit must be 1–90; obey PMC daytime request guidance.')
    ranked=rank();rows={r['id']:r for r in load()};mapping=pmcid_map();CACHE.mkdir(exist_ok=True)
    reviews=[];audit=[];attempts=0
    prior=c.ROOT/'config'/'automated_reviews.jsonl'
    existing={}
    if prior.exists():existing={r['id']:r for r in map(json.loads,prior.read_text().splitlines()) if r}
    # Balance strong identity cases and plausible joint-first cases.
    identities=[x for x in ranked if (x['identity_assessment'] or {}).get('score',0)>=35]
    cofirst=[x for x in ranked if x['reason']=='equal_contribution_group_ambiguous' and any(a['position']<=5 and a['equal_contrib']=='Y' for a in x['dceg_later_authors'])]
    chosen=[];seen=set()
    for i in range(max(len(identities),len(cofirst))):
        for group in [identities,cofirst]:
            if i<len(group) and group[i]['id'] not in seen:
                chosen.append(group[i]);seen.add(group[i]['id'])
    for x in chosen:
        r=rows[x['id']];pmcid=mapping.get(r['pmid'])
        if not pmcid or r['id'] in existing:continue
        path=CACHE/(pmcid+'.xml.gz')
        if not path.exists() and attempts>=limit:continue
        url='https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/?'+urlencode({'verb':'GetRecord','identifier':'oai:pubmedcentral.nih.gov:'+pmcid.removeprefix('PMC'),'metadataPrefix':'pmc_fm'})
        if path.exists():data=gzip.decompress(path.read_bytes())
        else:
            attempts+=1
            try:data=c.curl(url,{'Accept-Encoding':'identity'})
            except RuntimeError as e:
                audit.append({'id':r['id'],'pmcid':pmcid,'error':str(e)});continue
            root=ET.fromstring(data)
            if root.find('.//{*}body') is not None:raise RuntimeError('Metadata-only endpoint unexpectedly returned body text')
            path.write_bytes(gzip.compress(data,mtime=0))
            path.with_suffix('.provenance.json').write_text(json.dumps({'url':url,'retrieved_at':c.now(),'sha256':hashlib.sha256(data).hexdigest()},indent=2))
            time.sleep(0.4)
        f=frontmatter(ET.fromstring(data));review=decide(r,f,url)
        if review:
            review['pmcid']=pmcid
            existing[r['id']]=review;reviews.append(review)
        audit.append({'id':r['id'],'pmcid':pmcid,'ranking':x,'decision':review,
                      'front_matter':f,'source_url':url})
        print(f'Front matter {len(audit)} ({attempts} new requests): {r["id"]} → '+(review['status'] if review else 'still unresolved'),flush=True)
    prior.write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n' for k,v in sorted(existing.items())))
    (OUT/'frontmatter_investigation.jsonl').write_text(''.join(json.dumps(a,ensure_ascii=False)+'\n' for a in audit))
    print(json.dumps({'new_requests':attempts,'reviewed':len(audit),'decisions_this_run':dict(Counter(r['status'] for r in reviews)),
                      'all_saved_decisions':len(existing)},indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['rank','investigate','resolve-cached','corroborate','report']);p.add_argument('--limit',type=int,default=90)
    a=p.parse_args()
    if a.command=='rank':rank()
    elif a.command=='resolve-cached':resolve_cached()
    elif a.command=='corroborate':corroborate_identities()
    elif a.command=='report':report()
    else:investigate(a.limit)
