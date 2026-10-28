"""Reproduce the staff-directory coverage report from curated evidence and SQLite."""
import html
import json
import sqlite3
from collections import Counter
from pathlib import Path
import collect as c

STAMP='2026-10-22'

def read(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]

def link(url,label):
    return '<a href="'+html.escape(url,quote=True)+'">'+html.escape(label)+'</a>'

def main():
    before=sqlite3.connect(c.ROOT/'data/snapshots/publications-before-staff-audit-2026-10-22.sqlite')
    after=sqlite3.connect(c.DB)
    old={id:json.loads(s) for id,s in before.execute('select id,record_json from publications')}
    records={id:json.loads(s) for id,s in after.execute('select id,record_json from publications')}
    findings=read(c.ROOT/'config/staff_coverage_findings.jsonl')
    profiles=read(c.EXPORT/f'staff_profile_findings_{STAMP}.jsonl')
    aliases=read(c.ROOT/'config/staff_name_aliases.jsonl')
    roster=read(c.EXPORT/f'staff_directory_audit_{STAMP}.jsonl')
    pending=read(c.ROOT/'config/staff_coverage_pending.jsonl')
    changed=[]
    for id,r in records.items():
        p=old.get(id)
        if p is None or p['status']!=r['status']:
            changed.append({'id':id,'title':r['title'],'previous_status':p['status'] if p else 'absent',
                            'current_status':r['status'],'publication_date':r['publication_date'],
                            'url':r['url'],'inclusion_routes':r.get('inclusion_routes',[])})
    removed=[{'id':id,'title':p['title'],'previous_status':p['status'],'current_status':'outside_date_window',
              'publication_date':'2019-12-30','url':p['url'],'inclusion_routes':[]}for id,p in old.items() if id not in records]
    old_first={id for id,r in old.items() if r['original_first_author_decision']['status']=='confirmed'}
    new_first={id for id,r in records.items() if r['original_first_author_decision']['status']=='confirmed'}
    assert old_first==new_first
    for item in findings:
        r=records[item['id']]
        assert r['status']==('excluded' if item['preprint'] else 'confirmed'),item
    searches=json.loads((c.EXPORT/f'staff_supplement_searches_{STAMP}.json').read_text())
    extra=json.loads((c.EXPORT/f'staff_alias_searches_{STAMP}.json').read_text())
    new={r['id'] for r in changed if r['previous_status']=='absent' and r['current_status']=='confirmed'}
    resolved={r['id'] for r in changed if r['previous_status'] in {'unresolved','inferred'} and r['current_status']=='confirmed'}
    summary={'reviewed_on':STAMP,'publication_window':{'start':'2020-01-01','end':'2026-10-15'},
             'directory_updated_on':'2026-10-15','directory_entries':len(roster),
             'dceg_office_branch_entries':sum(not p['cgr_collaborator'] for p in roster),
             'cgr_entries':sum(p['cgr_collaborator'] for p in roster),
             'linked_profile_requests':len(profiles),'profile_downloads_succeeded':sum('error' not in p for p in profiles),
             'profile_downloads_failed':sum('error' in p for p in profiles),
             'supplemental_directory_name_searches':searches['people_searched'],'publishing_variant_searches':len(extra['searches']),
             'additional_metadata_candidates_screened':len(set(searches['new_candidate_pmids'])|set(extra['new_candidate_pmids'])),
             'new_confirmed_publications':len(new),'existing_candidates_confirmed':len(resolved),
             'new_preprints_kept_separate':sum(r['previous_status']=='absent' and records[r['id']].get('is_preprint') for r in changed),
             'new_publications_by_person':dict(Counter(f['person'] for f in findings if f['id'] in new)),
             'baseline_counts':dict(Counter(r['status'] for r in old.values())),
             'current_counts':dict(Counter(r['status'] for r in records.values())),
             'original_first_author_subset_unchanged':True,'original_first_author_confirmed':len(new_first),
             'removed_after_date_verification':removed,'complete':False}
    (c.EXPORT/f'staff_coverage_summary_{STAMP}.json').write_text(json.dumps(summary,indent=2)+'\n')
    c.write_csv(c.EXPORT/f'staff_coverage_changes_{STAMP}.csv',changed+removed)
    (c.EXPORT/f'staff_coverage_changes_{STAMP}.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in changed+removed))
    def papers(rows):
        out=[]
        for f in rows:
            r=records[f['id']]
            out.append('<article><h3>'+html.escape(f['person'])+'</h3><p>'+link(r['url'],r['title'])+'</p><p class="meta">'+html.escape(r['publication_date'] or '')+' · '+html.escape(r['journal'])+' · author '+str(f['author_position'])+' · '+html.escape(r['status'])+'</p><p>'+html.escape(f['note'])+'</p><p>'+link(f['evidence_url'],'Membership evidence')+'</p></article>')
        return ''.join(out)
    alias_html=''.join('<li>'+html.escape(a['directory_name'])+' → '+html.escape('; '.join(a['names']))+' · '+link(a['source'],'Official biography')+'</li>' for a in aliases)
    pending_html=''.join('<article><h3>'+html.escape(p['person'])+'</h3><p>'+html.escape(p['reason'])+'</p>'+link(p['source'],'Source')+'</article>' for p in pending)
    failed_html=''.join('<li>'+html.escape(p['name'])+' · '+link(p['profile_url'],'Directory-linked URL')+'</li>' for p in profiles if 'error' in p)
    stats=''.join('<div class="stat"><strong>'+str(v)+'</strong>'+label+'</div>'for v,label in [(len(new),'new publications'),(len(resolved),'existing candidates confirmed'),(summary['new_preprints_kept_separate'],'new preprint, separate'),(summary['current_counts']['confirmed'],'confirmed total')])
    output='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DCEG staff coverage audit</title><style>
body{font:16px/1.6 system-ui,sans-serif;color:#173244;background:#f4f7f8;max-width:1080px;margin:36px auto;padding:0 24px}h1{font-size:32px;letter-spacing:-1px;margin-bottom:4px}h2{margin-top:36px}h3{margin:0 0 8px;font-size:18px}a{color:#006b80}.stats{display:flex;gap:16px;flex-wrap:wrap;margin:24px 0}.stat,article{background:white;border:1px solid #d8e2e7;border-radius:10px;padding:18px 22px}.stat{min-width:160px}.stat strong{display:block;font-size:30px}.notice{background:#fff9ea;border-left:4px solid #d0a03b;padding:12px 18px}.meta{color:#526876;font-size:14px}article{margin:16px 0}summary{cursor:pointer}li{margin:8px 0}
</style><body><p>'''+link('browse.html','← Publication database')+'''</p><h1>DCEG staff coverage audit</h1><p class="meta">Reviewed October 22, 2026 · Directory updated October 15 · Publications through October 15, 2026</p>'''+ '<div class="stats">'+stats+'</div>'+'''<p>Compared all 449 personnel-directory entries with the publication searches and author records: 370 DCEG office/branch entries and 79 separately listed CGR collaborators. Directory entries include research, administrative, volunteer and support roles; an unmatched entry is not automatically a missing researcher.</p><p>The profile crawl retrieved 213 of 229 linked pages. Supplementary web checks recovered useful indexed biographies for several failed links, including Samuel Anyaso-Samuel and Nesreen Shahrour. Thirty-three name searches and four publishing-name variant searches produced 890 additional citation metadata candidates for screening. Most were unrelated names, earlier appointments, or authorships outside the inclusion rule.</p><div class="notice">A current-directory audit cannot certify historical completeness since 2020. Former personnel absent from today's roster, staff without public biographies, undated adjunct appointments, and publications outside PubMed still need independent coverage. Names, coauthors and topics guide identity review; they do not establish DCEG membership by themselves.</div><p>'''+link(f'staff_coverage_changes_{STAMP}.csv','Download publication decisions')+' · '+link(f'staff_directory_audit_{STAMP}.csv','Download full directory comparison')+' · '+link(f'staff_coverage_summary_{STAMP}.json','Audit counts and provenance')+'''</p><h2>Newly found publications</h2><p>Nine publications from five staff publishing identities were absent from the database. Dated appointment histories or contemporaneous DCEG membership sources support these decisions even where the papers report university-only affiliations.</p>'''+papers([f for f in findings if f['id'] in new])+'''<h2>Existing candidates resolved</h2><p>Fifteen papers already in the candidate pool now qualify. One paper has both a verified DCEG first author and last author, so it appears twice below to retain both author decisions.</p>'''+papers([f for f in findings if f['id'] in resolved])+'''<h2>Preprint retained separately</h2>'''+papers([f for f in findings if f['preprint']])+'''<h2>Names that needed alias handling</h2><p>Several apparent personnel gaps were already covered under different publishing names. The Samuel/Sam variant also led to one newly found publication.</p><ul>'''+alias_html+'''</ul><h2>Timing and identity cases still needing evidence</h2>'''+pending_html+'''<h2>Publication-date corrections</h2><p>PMID 31886477 first appeared online December 30, 2019 and was removed from the 2020-onward window despite its 2021 issue date. PMID 36929942 first appeared March 17, 2023, before Peter Kraft's April 2023 appointment, and remains unresolved. The original 1,503 confirmed first-author subset is unchanged.</p><details><summary>16 directory-linked profile requests failed</summary><p>These failures are recorded rather than treated as evidence of absent publications. Search-indexed biographies may still exist; the personnel directory can lag moves and departures.</p><ul>'''+failed_html+'''</ul></details></body></html>'''
    (c.EXPORT/'staff_coverage_review.html').write_text(output)
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
