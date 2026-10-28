"""Cache the public NIH researcher biographies linked from the staff audit."""
import gzip
import hashlib
import json
import re
import time
from urllib.parse import urlparse,urljoin
import collect as c
import audit_staff as a


def main():
    (c.RAW/'staff-profiles').mkdir(exist_ok=True)
    staff=a.roster();out=[]
    for person in staff:
        url=person['profile_url']
        if not url:continue
        if urlparse(url).hostname not in {'dceg.cancer.gov','irp.nih.gov','staffprofiles.cancer.gov','ccr.cancer.gov'}:
            raise ValueError('Unexpected non-NIH profile URL: '+url)
        name='staff-profiles/'+hashlib.sha256(url.encode()).hexdigest()[:20]+'.html'
        was_cached=(c.RAW/(name+'.gz')).exists()
        try:
            raw=c.cached(name,url).decode()
            m=re.search(r'<main\b[^>]*>(.*?)</main>',raw,re.S|re.I)
            main=m.group(1) if m else raw
            main=re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>',' ',main,flags=re.S|re.I)
            heading=re.search(r'<h1\b[^>]*>(.*?)</h1>',main,re.S|re.I)
            text=c.plain(main)
            links=[{'text':c.plain(label),'url':urljoin(url,href)} for href,label in re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>',main,re.S|re.I)]
            date_sentences=[s.strip() for s in re.split(r'(?<=[.!?])\s+',text) if re.search(r'\b(?:19|20)\d{2}\b',s) and re.search(r'join|appoint|fellow|DCEG|Division of Cancer|retir|depart|leav|since|return',s,re.I)]
            out.append({**{k:person[k] for k in ['name','branch','role','profile_url','cgr_collaborator']},
                        'heading':c.plain(heading.group(1)) if heading else '',
                        'biography_text':text,'dated_career_sentences':date_sentences,
                        'publication_and_identifier_links':[x for x in links if any(s in x['url'] for s in ['pubmed','orcid','publications'])],
                        'raw_cache':'data/raw/'+name+'.gz','retrieved_on':a.STAMP})
        except RuntimeError as e:
            out.append({'name':person['name'],'profile_url':url,'error':str(e)})
        if len(out)%20==0:print(f'Profiles checked: {len(out)}',flush=True)
        if not was_cached:time.sleep(0.35)
    (c.EXPORT/f'staff_profile_findings_{a.STAMP}.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in out))
    print(json.dumps({'profiles_checked':len(out),'errors':sum('error' in x for x in out)},indent=2),flush=True)


if __name__=='__main__':main()
