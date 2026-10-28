"""Supplement discovery for directory researchers absent from the first-author seeds.

Searches are candidates only and cover all author positions. Metadata are cached
for examination; the discovery manifest is not changed by this script.
"""
import json
import time
from urllib.parse import urlencode
import collect as c


def main():
    staff=[json.loads(s) for s in (c.EXPORT/'staff_directory_audit_2026-10-22.jsonl').read_text().splitlines()]
    gaps=[p for p in staff if not p['cgr_collaborator'] and p['profile_url'] and
          (not p['first_author_search_seed_present'] or not p['recorded_publications'])]
    results=[];all_ids=set()
    for ix,p in enumerate(gaps):
        variants=[p['last_name']+' '+p['first_name'].split()[0]]
        if p['name']=='Jackie A. Lavigne':variants+=['Lavigne JA','Lavigne Jacqueline']
        term='('+' OR '.join('"'+v+'"[Author]' for v in variants)+') AND ("2020/01/01"[dp] : "2027/12/31"[dp])'
        ids=c.search_ids(f'staff-search-20261022-{ix:03}',term)
        all_ids.update(ids)
        results.append({'name':p['name'],'profile_url':p['profile_url'],'query':term,'pmids':ids,'count':len(ids),'searched_on':'2026-10-22','status':'unverified_candidates'})
        print(p['name']+': '+str(len(ids))+' candidate citations',flush=True)
    known=set(json.loads((c.RAW/'discovery.json').read_text())['pmids'])
    new=sorted(all_ids-known,key=int)
    for ix in range(0,len(new),100):
        c.cached(f'pubmed-records-staff-20261022-{ix:04}.xml',c.EUTILS+'efetch.fcgi?'+urlencode({'db':'pubmed','id':','.join(new[ix:ix+100]),'retmode':'xml'}))
        time.sleep(0.4)
    report={'people_searched':len(gaps),'candidate_pmids':len(all_ids),'new_candidate_pmids':new,'searches':results,
            'note':'New metadata are candidates, not confirmed membership. Earliest publication dates and identities require assessment.'}
    (c.EXPORT/'staff_supplement_searches_2026-10-22.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'people_searched':len(gaps),'unique_citations':len(all_ids),'new_citations':len(new)},indent=2),flush=True)


if __name__=='__main__':main()
