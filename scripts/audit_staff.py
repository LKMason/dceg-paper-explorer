"""Compare the public DCEG personnel directory with searched and recorded authors.

Directory presence and name matches are discovery leads, not historical membership
proof. CGR personnel are reported separately from DCEG branch personnel.
"""
from collections import Counter
import gzip
import html
import json
from pathlib import Path
import re
import sqlite3
import collect as c

STAMP='2026-10-22'
SOURCE='https://dceg2.cancer.gov/cgi-bin/Personnel.pl?1=ALL'


def roster():
    raw=gzip.decompress((c.RAW/f'staff-directory-{STAMP}.html.gz').read_bytes()).decode()
    rows=[]
    for tr in re.findall(r'<tr class="staff-contact">(.*?)</tr>',raw,re.S):
        cells=re.findall(r'<td[^>]*>(.*?)</td>',tr,re.S)
        if len(cells)!=4:raise ValueError('Unexpected directory table layout')
        text=c.plain(cells[0]);role=re.search(r'\(([^()]*)\)\s*$',text)
        name=re.split(r'<span',cells[0])[0];name=c.plain(name)
        pieces=name.split(',');surname=pieces[0].strip();given=pieces[1].strip() if len(pieces)>1 else ''
        profile=re.search(r'<a href="([^"]+)"',cells[0])
        rows.append({'name':(given+' '+surname).strip(),'directory_name':name,
                     'first_name':given,'last_name':surname,'branch':c.plain(cells[1]),
                     'role':role.group(1) if role else '', 'employment_type':c.plain(cells[3]),
                     'profile_url':html.unescape(profile.group(1)) if profile else '',
                     'directory_url':SOURCE,'directory_updated_on':'2026-10-15',
                     'cgr_collaborator':c.plain(cells[1])=='CGR'})
    assert len(rows)>200
    return rows


def identity(last,first):
    return c.norm(last).replace(' ',''),c.norm(first.split()[0]).replace(' ','') if first.split() else ''


def compatible_given(a,b):
    left=c.norm(a).split();right=c.norm(b).split()
    if not left or not right:return False
    if ''.join(left)==''.join(right):return True
    if not all(x==y or (min(len(x),len(y))==1 and x[0]==y[0]) for x,y in zip(left,right)):return False
    return all(len(x)==1 for x in left[min(len(left),len(right)):] + right[min(len(left),len(right)):])


def main():
    staff=roster();con=sqlite3.connect(c.DB)
    records=[json.loads(s) for s, in con.execute('select record_json from publications')]
    seeds=json.loads((c.RAW/'discovery.json').read_text())['seed_names']
    aliases={r['directory_name']:r for r in map(json.loads,(c.ROOT/'config'/'staff_name_aliases.jsonl').read_text().splitlines())}
    seedkeys={identity(s.rsplit(' ',1)[0],s.rsplit(' ',1)[1]) for s in seeds}
    index={};initial_index={}
    for r in records:
        if r.get('is_preprint') or r.get('is_conference_abstract'):continue
        for pos,a in enumerate(r['authors'],1):
            if a.get('collective'):continue
            key=identity(a['last_name'],a['first_name'])
            item={'id':r['id'],'title':r['title'],'publication_date':r['publication_date'],
                  'matched_author':a['name'],'position':pos,'author_count':len(r['authors']),
                  'first_name':a['first_name'],
                  'endpoint':pos==len(r['authors']) or pos in r['first_author_positions'],
                  'status':r['status'],'affiliations':a['affiliations'],'orcid':a.get('orcid'),
                  'researcher_id':a.get('researcher_id'),'url':r['url']}
            index.setdefault(key,[]).append(item)
            initial_index.setdefault((key[0],key[1][:1]),[]).append(item)
    for person in staff:
        key=identity(person['last_name'],person['first_name'])
        alias=aliases.get(person['name'],{})
        identities=[(key,person['first_name'])]
        for name in alias.get('names',[]):
            first,last=name.rsplit(' ',1);identities.append((identity(last,first),first))
        person['verified_name_aliases']=alias.get('names',[])
        person['alias_source']=alias.get('source')
        person['first_author_search_seed_present']=any((key[0],key[1][:1]) in seedkeys for key,_ in identities)
        matched={(r['id'],r['position']):r for key,given in identities for r in initial_index.get((key[0],key[1][:1]),[]) if compatible_given(given,r['first_name'])}
        person['matches']=list(matched.values())
        person['initial_only_matches']=[x for x in initial_index.get((key[0],key[1][:1]),[]) if x not in person['matches']]
        person['recorded_publications']=len({r['id'] for r in person['matches']})
        person['confirmed_publications']=len({r['id'] for r in person['matches'] if r['status']=='confirmed'})
        person['open_endpoint_publications']=len({r['id'] for r in person['matches'] if r['endpoint'] and r['status'] in {'unresolved','inferred'}})
    staff.sort(key=lambda p:(p['cgr_collaborator'],-p['open_endpoint_publications'],p['confirmed_publications']>0,p['name']))
    output=c.EXPORT/f'staff_directory_audit_{STAMP}.jsonl'
    output.write_text(''.join(json.dumps(p,ensure_ascii=False)+'\n' for p in staff))
    fields=['name','branch','role','employment_type','profile_url','cgr_collaborator',
            'first_author_search_seed_present','recorded_publications','confirmed_publications','open_endpoint_publications']
    c.write_csv(output.with_suffix('.csv'),[{k:p[k] for k in fields} for p in staff])
    dceg=[p for p in staff if not p['cgr_collaborator']]
    summary={'directory_entries':len(staff),'dceg_office_branch_entries':len(dceg),'cgr_entries':len(staff)-len(dceg),
             'linked_profiles':sum(bool(p['profile_url']) for p in staff),
             'dceg_names_with_compatible_given_name_matches':sum(bool(p['matches']) for p in dceg),
             'dceg_names_with_open_endpoint_papers':sum(p['open_endpoint_publications']>0 for p in dceg),
             'dceg_names_without_first_author_search_seed':sum(not p['first_author_search_seed_present'] for p in dceg),
             'caution':'Names and directory roles are leads. Current membership does not establish earlier affiliation, and CGR listing alone does not establish DCEG membership.'}
    output.with_name(f'staff_directory_summary_{STAMP}.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    for p in dceg:
        if p['open_endpoint_publications'] or (not p['first_author_search_seed_present'] and p['profile_url']):
            print(json.dumps({k:p[k] for k in fields},ensure_ascii=False))


if __name__=='__main__':main()
