#!/usr/bin/env python3
"""Apply and report the frozen 283-candidate membership review.

Search hits are discovery leads. Only individually curated findings or explicit
publisher metadata can confirm membership. Uncertain decisions remain open.
"""
from collections import Counter
import copy
import gzip
import html
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
import collect as c
import investigate as i

BATCH = 'membership_283_2026-10-15'
MANIFEST = c.ROOT/'config'/'membership_review_batch_2026-10-15.json'
REVIEWS = c.ROOT/'config'/'membership_batch_reviews.jsonl'


def read_lines(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s] if path.exists() else []


def publisher_metadata():
    found = {}
    for path in i.CACHE.glob('*.xml.gz'):
        root = i.strip_namespaces(ET.fromstring(gzip.decompress(path.read_bytes())))
        pmid = next((a.text for a in root.findall('.//article-id') if a.get('pub-id-type')=='pmid'),None)
        if pmid:
            found['pmid:'+pmid] = (i.frontmatter(root),json.loads(path.with_suffix('.provenance.json').read_text())['url'])
    return found


def prepare():
    batch = json.loads(MANIFEST.read_text())['records']
    assert len(batch)==283 and len({r['id'] for r in batch})==283
    findings = {r['name']:r for r in read_lines(c.ROOT/'config'/'membership_author_findings.jsonl')}
    prior = {r['id']:r for r in read_lines(c.ROOT/'config'/'reviews.jsonl')}
    cached = publisher_metadata()
    reviews = []
    for candidate in batch:
        r = copy.deepcopy(candidate['original_record'])
        key = r['id']; finding = findings.get(candidate['first_author'])
        positive = key in (finding or {}).get('confirm',[])
        excluded = key in (finding or {}).get('exclude',[])
        status = r['status']
        outcome = (finding or {}).get('outcome','membership_evidence_inconclusive')
        sources = list((finding or {}).get('sources',[]))
        note = (finding or {}).get('note','')
        positions = []
        method = 'membership_batch_review'
        basis = None
        extra = {}
        fresh,_,evidence,first_group = c.evaluate(r)
        if key=='pmid:40471647':
            status='excluded';outcome='duplicate_reconciled'
            note='Exact normalized title and all 15 authors in the same order match canonical PMID 42564971. The canonical record is already confirmed; consolidate the old PMID and DOI as aliases, rather than counting another publication.'
            sources=['https://pubmed.ncbi.nlm.nih.gov/42564971/']
            extra['duplicate_of']='pmid:42564971'
        elif positive:
            status='confirmed';outcome='confirmed'
            method='membership_batch_historical_review';basis='verified_historical_membership'
            positions=[1]
            if finding.get('outcome')=='published_author_metadata':
                basis='published_author_metadata';method='membership_batch_published_metadata'
            if finding.get('qualifying_author_names'):
                positions=[p for p,a in enumerate(r['authors'],1) if c.norm(a['name']) in {c.norm(n) for n in finding['qualifying_author_names']}]
                assert len(positions)==len(finding['qualifying_author_names'])
            if finding.get('verified_publication_date'):
                extra['verified_publication_date']=finding['verified_publication_date']
            if key in cached:
                front,url=cached[key]
                extra['source_author_notes']=front['author_notes']
                decision=i.decide(r,front,url)
                sources.append(url)
                if decision and decision['status']=='confirmed':
                    extra.update({k:decision[k] for k in ['verified_affiliations'] if k in decision})
        elif excluded:
            status='excluded';outcome='excluded'
            if finding.get('verified_publication_date'):
                extra['verified_publication_date']=finding['verified_publication_date']
            if key in cached:
                extra['source_author_notes']=cached[key][0]['author_notes']
                sources.append(cached[key][1])
                note+=' Publisher author notes were checked from the cached metadata-only response.'
        elif fresh=='confirmed' and r['date_scope']=='inside':
            status='confirmed';outcome='confirmed';method='membership_batch_affiliation_parser_review'
            basis='published_affiliation'
            positions=sorted({e['author_position'] for e in evidence if e['level']=='confirmed' and e['author_position'] in first_group})
            note='Published first-author affiliation explicitly identifies DCEG. Reviewed formatting that the parser previously missed: '+ '; '.join(e['text'] for e in evidence if e['level']=='confirmed' and e['author_position'] in positions)
            sources=[r['url']]
        elif key in cached:
            front,url = cached[key]
            decision=i.decide(r,front,url)
            if decision and decision['status']=='confirmed':
                status='confirmed';outcome='confirmed';method='membership_batch_published_metadata';basis='published_author_metadata'
                positions=decision['qualifying_author_positions'];note=decision['note'];sources=[url]+sources
                extra.update({k:decision[k] for k in ['verified_affiliations','source_author_notes'] if k in decision})
        if outcome not in {'confirmed','excluded','duplicate_reconciled'}:
            # A specific different NIH unit is contrary evidence, not merely a
            # generic NIH affiliation. Do not infer DCEG from a historical tag.
            if candidate['other_nih_unit_flag'] and status=='inferred':
                status='unresolved'
            aff='; '.join(candidate['reported_affiliations']) or '[No affiliation supplied in the citation]'
            note=(note+' ' if note else '')+f"Reviewed first author {candidate['first_author']} on this {candidate['publication_date'] or 'undated'} publication. Citation affiliation: {aff}. "
            note+='Researcher-name, ORCID, branch annotation and coauthor signals were checked against the candidate references; none alone establishes DCEG membership for this paper. '
            if status=='inferred' and key in prior:
                note+='Previous sourced assessment retained: '+prior[key]['note']+' '
                sources += [prior[key]['evidence_url']]
            note+='A targeted personnel-source search was performed. Search matches with unverified identity were not used as proof. '
            if outcome in {'former_member_outside_period','publication_precedes_membership','external_collaborator','external_first_author_membership_not_established'}:
                note+='Next check: a paper-specific DCEG link, researcher confirmation, or a documented DCEG co-first author. Former employment alone is insufficient under the user-confirmed policy.'
            else:
                note+='Next check: publication-specific DCEG membership/appointment dates or a documented DCEG co-first author.'
        review={'id':key,'status':status,'qualifying_author_positions':positions,
                'author_positions_reviewed':[1],'note':note,'evidence_url':sources[0] if sources else r['url'],
                'supporting_sources':list(dict.fromkeys(sources+[r['url']])),
                'reviewed_on':'2026-10-15','method':method,'review_batch':BATCH,
                'membership_review_outcome':outcome,
                'evidence_kind':'verified_historical_membership' if basis=='verified_historical_membership' else 'published_author_metadata_review' if status=='confirmed' else 'membership_review_assessment',
                'personnel_finding_verified':bool(finding),
                'publisher_metadata_checked':key in cached,**extra}
        if basis:review['confirmation_basis']=basis
        reviews.append(review)
    REVIEWS.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in reviews))
    print(json.dumps({'reviewed':len(reviews),'proposed_statuses':dict(Counter(r['status'] for r in reviews)),
                      'outcomes':dict(Counter(r['membership_review_outcome'] for r in reviews)),
                      'cached_publisher_metadata_checked':sum(r['publisher_metadata_checked'] for r in reviews)},indent=2))


def report():
    batch=json.loads(MANIFEST.read_text())['records']
    reviewed={r['id']:r for r in read_lines(REVIEWS)}
    db=sqlite3.connect(c.DB)
    current={r['id']:r for (s,) in db.execute('SELECT record_json FROM publications') for r in [json.loads(s)]}
    results=[]
    for original in batch:
        review=reviewed[original['id']]
        canonical=review.get('duplicate_of',original['id'])
        actual=current.get(canonical)
        # This is the historical first-author review, independent of subsequent
        # scope changes. The main browser exposes the current broader policy.
        historical=(actual or {}).get('original_first_author_decision',actual)
        result={k:original[k] for k in ['id','title','first_author','publication_date','lead_type']}
        result.update({'previous_status':original['status'],'current_status':historical['status'] if historical else 'outside_date_window',
                       'review_outcome':review['membership_review_outcome'],'canonical_id':canonical,
                       'publication_url':original['original_record']['url'],
                       'reviewed_publication_date':review.get('verified_publication_date',original['publication_date']),
                       'evidence_url':review['evidence_url'],'supporting_sources':review['supporting_sources'],
                       'note':review['note'],'historical_source_review':review['personnel_finding_verified'],
                       'publisher_metadata_checked':review['publisher_metadata_checked']})
        results.append(result)
    assert len(results)==283
    (c.EXPORT/'membership_review_results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in results))
    c.write_csv(c.EXPORT/'membership_review_results.csv',[{**r,'supporting_sources':'; '.join(r['supporting_sources'])} for r in results])
    summary={'reviewed_candidates':len(results),'people_searched':188,
             'newly_confirmed_publications':sum(r['review_outcome']=='confirmed' and r['current_status']=='confirmed' for r in results),
             'excluded_publications':sum(r['review_outcome']=='excluded' and r['current_status']=='excluded' for r in results),
             'outside_date_window':sum(r['current_status']=='outside_date_window' for r in results),
             'duplicate_reconciliations':sum(r['review_outcome']=='duplicate_reconciled' for r in results),
             'still_open':sum(r['current_status'] in {'inferred','unresolved'} for r in results),
             'historical_source_review_candidates':sum(r['historical_source_review'] for r in results),
             'publisher_metadata_checked':sum(r['publisher_metadata_checked'] for r in results),
             'database_counts':dict(Counter(r.get('original_first_author_decision',r)['status'] for r in current.values())),
             'policy_scope':'original_first_author',
             'scope_note':'All candidates received citation/identity screening and a targeted personnel-source search. Individually verified historical sources and publisher author metadata were used where available. Inconclusive searches were not treated as proof of membership or nonmembership.',
             'former_author_policy':'After departure, require a paper-specific DCEG link or researcher confirmation; historical employment alone is insufficient.'}
    (c.EXPORT/'membership_review_batch_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    results.sort(key=lambda r:(0 if r['review_outcome']=='confirmed' else 2 if r['current_status'] in {'inferred','unresolved'} else 1,r['first_author']))
    table=''.join('<tr><td>'+html.escape(r['first_author'])+'<br><small>'+html.escape(r['reviewed_publication_date'] or '')+'</small></td><td><a href="'+html.escape(r['publication_url'],quote=True)+'">'+html.escape(r['title'])+'</a></td><td>'+html.escape(r['current_status'].replace('_',' '))+'<br><small>'+html.escape(r['review_outcome'].replace('_',' '))+'</small></td><td>'+html.escape(r['note'])+'<p>'+ ' · '.join('<a href="'+html.escape(url,quote=True)+'">Source '+str(n)+'</a>' for n,url in enumerate(r['supporting_sources'],1))+'</p></td></tr>' for r in results)
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DCEG membership review — 283 candidates</title><style>body{font:15px/1.55 system-ui,sans-serif;color:#173244;background:#f4f7f8;margin:32px auto;padding:0 24px;max-width:1500px}h1{line-height:1.2}a{color:#006b80}input{padding:12px;font:inherit;width:min(95%,650px)}table{border-collapse:collapse;background:white;width:100%;margin-top:24px}th,td{padding:14px;text-align:left;vertical-align:top;border:1px solid #d8e2e7}th{background:#e8f1f2}td:last-child{min-width:300px}small{color:#526876}.stats{background:white;padding:18px;border:1px solid #d8e2e7;border-radius:8px}.wrap{overflow:auto}</style><h1>Original first-author membership review</h1><p class="stats">This review uses the previous first/co-first-author policy. The publication database now also includes qualifying DCEG last authors and DCEG coauthors on NIH-first-author papers.</p><p>283 candidates · 188 researcher names · reviewed October 15, 2026</p><p class="stats">SUMMARY</p><p>All candidates received citation and identity screening and a targeted personnel-source search. Historical membership was verified individually where sources supported it. Open cases still require evidence; search results alone were not treated as proof.</p><p><a href="browse.html">Publication database</a> · <a href="membership_review_results.csv">Download the complete review</a></p><input id="q" aria-label="Search review results" placeholder="Search author, title, decision, or evidence…"><p id="count">283 records</p><div class="wrap"><table><thead><tr><th>First author</th><th>Publication and source</th><th>Outcome</th><th>Evidence and next check</th></tr></thead><tbody>TABLE</tbody></table></div><script>const rows=[...document.querySelectorAll('tbody tr')];document.getElementById('q').oninput=e=>{const terms=e.target.value.toLowerCase().split(/\\s+/).filter(Boolean);let count=0;for(const r of rows){r.hidden=!terms.every(t=>r.textContent.toLowerCase().includes(t));if(!r.hidden)count++}document.getElementById('count').textContent=count+' records'};</script></html>'''
    counts=f"{summary['newly_confirmed_publications']} newly confirmed · {summary['excluded_publications']} excluded · {summary['outside_date_window']} outside the date window · {summary['duplicate_reconciliations']} duplicate consolidated · {summary['still_open']} still need evidence"
    (c.EXPORT/'membership_review.html').write_text(page.replace('SUMMARY',counts).replace('TABLE',table))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','report']);a=p.parse_args()
    prepare() if a.command=='prepare' else report()
