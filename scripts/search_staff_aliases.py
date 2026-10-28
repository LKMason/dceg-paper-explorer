"""Supplement staff discovery with verified publishing-name variants.

Retains public citation metadata as audit leads; does not change eligibility or
the discovery manifest. Cached queries reproduce the October 22, 2026 audit.
"""
import sys,json,gzip,time,xml.etree.ElementTree as ET
from urllib.parse import urlencode
sys.path.insert(0,'scripts');import collect as c
terms=[('Samuel Anyaso-Samuel','("Anyaso-Samuel S"[Author] OR "Anyaso-Samuel Samuel"[Author])'),('Padma Sheila Rajagopal','("Rajagopal PS"[Author] OR "Rajagopal Padma"[Author])'),('Wen-Yi Huang','"Huang Wen-Yi"[Author]'),('Haegin Han','"Han Haegin"[Author]')]
known=set(json.loads((c.RAW/'discovery.json').read_text())['pmids']);known.update(json.loads((c.EXPORT/'staff_supplement_searches_2026-10-22.json').read_text())['new_candidate_pmids'])
rows=[];allids=set()
for i,(name,query) in enumerate(terms):
 query+=' AND ("2020/01/01"[dp] : "2027/12/31"[dp])'
 ids=c.search_ids('staff-alias-search-20261022-'+str(i),query);allids.update(ids);rows.append({'name':name,'query':query,'pmids':ids});print(name,len(ids),flush=True);time.sleep(.4)
new=sorted(allids-known,key=int)
for i in range(0,len(new),100):
 c.cached('pubmed-records-staff-alias-20261022-'+str(i)+'.xml',c.EUTILS+'efetch.fcgi?'+urlencode({'db':'pubmed','id':','.join(new[i:i+100]),'retmode':'xml'}));time.sleep(.4)
(c.EXPORT/'staff_alias_searches_2026-10-22.json').write_text(json.dumps({'searches':rows,'new_candidate_pmids':new},indent=2)+'\n')
print('new',len(new),flush=True)
