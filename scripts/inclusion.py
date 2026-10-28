"""Evidence-based endpoint / NIH-first inclusion, independent of first-author review."""
import copy
import json
from pathlib import Path
import re
import unicodedata

ROOT=Path(__file__).resolve().parents[1]
POLICY=json.loads((ROOT/'config'/'inclusion_policy.json').read_text())
NIH=json.loads((ROOT/'config'/'nih_units.json').read_text())
ROUTE_LABELS={'dceg_first_author':'DCEG first author',
              'dceg_last_author':'DCEG last author',
              'dceg_coauthor_nih_first':'DCEG coauthor with NIH first author'}


def norm(s):
    s=unicodedata.normalize('NFKD',s or '').encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+',' ',s.replace('&',' and ')).strip()


def nih_affiliation(text):
    """Require an author affiliation, not an email, grant or former address."""
    if len(re.findall(r'(?:^|;)\s*\d+\s*(?=[A-Za-z])',text))>1:return None
    clean=re.sub(r'[\w.+-]+@[\w.-]+',' ',text)
    n=norm(clean)
    if re.search(r'\bformerly\b|\bformer address\b|\bprevious address\b',n):return None
    if re.search(r'\b(?:nih|nci)[ -]?(?:funded|supported|designated)\b|\bfunded by\b|\bsupported by\b|\bgrant (?:no|number)\b',n):return None
    # Accept normalized bibliographic abbreviations, but never rewrite names.
    words={'natl':'national','inst':'institute','insts':'institutes','hlth':'health','canc':'cancer'}
    n=' '.join(words.get(w,w) for w in n.split())
    usa=bool(re.search(r'\bnih\b|\bnational institutes? (?:of )?health\b|\bbethesda\b|\brockville\b|\bdurham\b|\bresearch triangle park\b|\bfrederick\b|\bbaltimore\b|\bphoenix\b|\bhamilton\b|\busa\b|\bunited states\b|\bmaryland\b',n))
    foreign=bool(re.search(r'\bmexico\b|\bthailand\b|\bjapan\b|\bchina\b|\bchinese\b|\bfrance\b|\bfrench\b|\bbrazil\b|\bindia\b|\bitaly\b|\bitalian\b|\bkorea\b|\btaiwan\b|\bpakistan\b|\bcolombia\b|\bargentina\b|\bchile\b|\begypt\b|\bcanada\b|\bcanadian\b|\bsri lanka\b|\bmalaysia\b|\bpoland\b|\baustralia\b|\bnetherlands\b|\bfinland\b|\bdenmark\b|\bsweden\b|\bnorway\b|\bunited kingdom\b',n))
    explicit_us=bool(re.search(r'\bbethesda\b|\brockville\b|\busa\b|\bunited states\b|\bmaryland\b',n))
    if foreign and not explicit_us:return None
    matched=None
    if re.search(r'\bnih\b|\bnational institutes? (?:of )?health\b',n):matched='NIH'
    for name in NIH['names']:
        if (' '+norm(name)+' ') in (' '+n+' '):
            if name in NIH['ambiguous_names'] and not usa:continue
            matched=name;break
    if not matched:
        for acronym in NIH['acronyms']:
            if re.search(r'\b'+acronym.lower()+r'\b',n) and (len(acronym)>3 or usa):
                matched=acronym;break
    if not matched:return None
    return {'kind':'first_author_published_nih_affiliation','level':'confirmed',
            'text':text,'matched_unit':matched,'source':'published_affiliation',
            'organization_sources':NIH['sources']}


def classify(record, affiliation_evidence):
    """Use publication-specific membership evidence; never propagate name matches."""
    authors=record['authors'];legacy=record['original_first_author_decision']
    verified={};evidence=[]
    for pos,a in enumerate(authors,1):
        if a.get('collective'):continue
        for aff in a.get('affiliations',[]):
            e=affiliation_evidence(aff)
            if e and e['level']=='confirmed':
                source_urls=[s for s in a.get('affiliation_sources',{}).get(aff,[]) if s.startswith('https://')]
                item={**e,'author_position':pos,'author':a['name'],
                      'source':source_urls[0] if source_urls else record['url'],
                      'metadata_sources':a.get('affiliation_sources',{}).get(aff,[]),
                      'confirmation_basis':'published_author_metadata' if source_urls else 'published_affiliation'}
                verified.setdefault(pos,[]).append(item)
    review=record.get('manual_review',{})
    if review.get('status')=='confirmed':
        for pos in review.get('qualifying_author_positions',[]):
            if 1<=pos<=len(authors) and not authors[pos-1].get('collective'):
                verified.setdefault(pos,[]).append({'level':'confirmed',
                    'kind':review.get('evidence_kind','reviewed_dceg_membership'),
                    'author_position':pos,'author':authors[pos-1]['name'],
                    'text':review['note'],'source':review['evidence_url'],
                    'confirmation_basis':review.get('confirmation_basis','published_author_metadata')})
    # Author-level historical reviews are publication-specific and independent
    # of the legacy first-author decision. They can verify any author position.
    for item in record.get('author_membership_reviews',[]):
        pos=item['author_position']
        if not (1<=pos<=len(authors)) or norm(item['author'])!=norm(authors[pos-1]['name']):
            raise ValueError('Author membership review does not match published author position: '+record['id'])
        if item['status']=='confirmed' and not authors[pos-1].get('collective'):
            verified.setdefault(pos,[]).append({'level':'confirmed','kind':'dated_staff_membership_review',
                'author_position':pos,'author':authors[pos-1]['name'],'text':item['note'],
                'source':item['evidence_url'],'confirmation_basis':item['confirmation_basis']})
    first=set(record.get('inclusion_first_author_positions',record.get('first_author_positions',[])))
    first={p for p in first if 1<=p<=len(authors) and not authors[p-1].get('collective')}
    first_qual=sorted(first & verified.keys())
    last_qual=[len(authors)] if authors and len(authors) in verified else []
    nih=[]
    if authors and not authors[0].get('collective'):
        for aff in authors[0].get('affiliations',[]):
            e=nih_affiliation(aff)
            if e:nih.append({**e,'author_position':1,'author':authors[0]['name'],
                             'source':next((s for s in authors[0].get('affiliation_sources',{}).get(aff,[]) if s.startswith('https://')),record['url'])})
    routes=[];qualifying=[]
    if first_qual:routes.append('dceg_first_author');qualifying+=first_qual
    if last_qual:routes.append('dceg_last_author');qualifying+=last_qual
    if not routes and verified and nih:
        routes=['dceg_coauthor_nih_first'];qualifying=sorted(verified)
    for pos in sorted(verified):evidence+=verified[pos]
    if routes==['dceg_coauthor_nih_first']:evidence+=nih
    status='confirmed' if routes else 'inferred' if legacy['status']=='inferred' else 'unresolved'
    if routes:reason=routes[0]
    elif verified:reason='dceg_coauthor_first_author_nih_requires_review'
    elif any(a.get('dceg_branches') for a in authors):reason='dceg_membership_requires_review'
    else:reason='no_verified_dceg_author'
    if record['date_scope']!='inside':status='unresolved';reason='publication_date_'+record['date_scope']
    if record.get('source_issues'):status='unresolved';reason='pubmed_identifier_requires_reconciliation'
    if status!='confirmed':
        evidence.append({'level':status,'kind':'inclusion_review_needed','author_position':None,'author':'',
                         'text':'No inclusion route is fully established: '+reason.replace('_',' ')+'. Existing first-author assessments are retained separately.','source':record['url']})
    else:
        for route in routes:
            evidence.append({'level':'confirmed','kind':'inclusion_rule','author_position':None,'author':'',
                             'text':ROUTE_LABELS[route]+'. Policy adopted '+POLICY['adopted_on']+'.',
                             'source':'config/inclusion_policy.json'})
    basis=None
    if status=='confirmed':
        primary_positions=first_qual if routes[0]=='dceg_first_author' else last_qual if routes[0]=='dceg_last_author' else qualifying
        basis=verified[primary_positions[0]][0]['confirmation_basis']
    return {'policy_id':POLICY['id'],'status':status,'reason':reason,
            'primary_route':routes[0] if status=='confirmed' else None,
            'routes':routes if status=='confirmed' else [],
            'qualifying_author_positions':sorted(set(qualifying)) if status=='confirmed' else [],
            'verified_dceg_author_positions':sorted(verified),
            'first_author_nih_affiliation_verified':bool(nih),
            'first_author_nih_evidence':nih,'confirmation_basis':basis,'evidence':evidence}


def apply(record, affiliation_evidence):
    record['original_first_author_decision']={k:copy.deepcopy(record.get(k)) for k in
        ['status','reason','confirmation_basis','first_author_positions','automatic_decision','evidence']}
    decision=classify(record,affiliation_evidence)
    record['inclusion_decision']=decision
    record['inclusion_routes']=decision['routes']
    record['qualifying_author_positions']=decision['qualifying_author_positions']
    record['status']=decision['status'];record['reason']=decision['reason'];record['evidence']=decision['evidence']
    record.pop('confirmation_basis',None)
    if decision['confirmation_basis']:record['confirmation_basis']=decision['confirmation_basis']
    return record
