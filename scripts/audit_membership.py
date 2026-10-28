#!/usr/bin/env python3
"""Find historical first-author membership leads without changing eligibility.

An external or missing paper affiliation does not rule out a DCEG appointment.
Catalogue branch annotations and coauthor networks are leads, not proof that an
appointment was held during a particular paper. Keep dates and contrary evidence.
"""
from collections import Counter, defaultdict
import json
import re
import collect as c
import investigate as i


OTHER_UNIT = re.compile(r'center for cancer research|national heart|nhlbi|national institute of allergy|niaid|division of cancer (?:control|prevention|treatment)|national library of medicine', re.I)


def audit(rows):
    by_name = defaultdict(list)
    by_orcid = defaultdict(list)
    for r in rows:
        if r.get('is_preprint'):
            continue
        reviewed = r.get('manual_review', {})
        for pos, a in enumerate(r['authors'], 1):
            kinds = []
            if any((c.affiliation_evidence(s) or {}).get('level') == 'confirmed' for s in a['affiliations']):
                kinds.append('published_dceg_affiliation')
            if a.get('dceg_branches'):
                kinds.append('catalogue_author_branch_annotation')
            if reviewed.get('status') == 'confirmed' and pos in reviewed.get('qualifying_author_positions', []):
                kinds.append(reviewed.get('confirmation_basis', 'reviewed_author_metadata'))
            if not kinds:
                continue
            ref = (r, a, kinds)
            by_name[i.identity(a)].append(ref)
            if i.orcid(a):
                by_orcid[i.orcid(a)].append(ref)
    leads = []
    for r in rows:
        if r['status'] not in {'unresolved', 'inferred'} or not r['authors'] or r.get('is_preprint') or r.get('is_conference_abstract'):
            continue
        a = r['authors'][0]
        coauthors = {i.identity(b) for b in r['authors'][1:]}
        refs = []
        seen = set()
        for ref, b, kinds in by_name[i.identity(a)] + by_orcid.get(i.orcid(a), []):
            if ref['id'] == r['id'] or ref['id'] in seen or not i.compatible(a, b):
                continue
            seen.add(ref['id'])
            shared = coauthors & {i.identity(x) for x in ref['authors'] if i.identity(x) != i.identity(b)}
            exact_orcid = bool(i.orcid(a) and i.orcid(a) == i.orcid(b))
            given = c.norm(a['first_name']).split()
            exact_name = i.identity(a) == i.identity(b) and bool(given) and len(given[0]) > 1
            if not exact_orcid and not (exact_name and len(shared) >= 2):
                continue
            target_year = (r.get('publication_date') or '')[:4]
            ref_year = (ref.get('publication_date') or '')[:4]
            distance = abs(int(target_year) - int(ref_year)) if target_year.isdigit() and ref_year.isdigit() else None
            refs.append({'publication': ref['url'], 'date': ref.get('publication_date'),
                         'author': b['name'], 'orcid_match': exact_orcid,
                         'shared_coauthors': sorted(' '.join(reversed(x)) for x in shared),
                         'year_distance': distance, 'evidence_kinds': kinds})
        refs.sort(key=lambda ref: (not ref['orcid_match'], ref['year_distance'] if ref['year_distance'] is not None else 999, -len(ref['shared_coauthors'])))
        annotation = bool(a.get('dceg_branches'))
        if not annotation and not refs:
            continue
        tier = 'first_author_catalogue_annotation' if annotation else 'orcid_linked_dceg_identity' if any(ref['orcid_match'] for ref in refs) else 'name_and_coauthor_network'
        leads.append({'id': r['id'], 'title': r['title'], 'first_author': a['name'],
                      'publication_date': r.get('publication_date'), 'status': r['status'],
                      'reason': r['reason'], 'lead_type': tier,
                      'author_branch_annotation': a.get('dceg_branches', []),
                      'reported_affiliations': a['affiliations'],
                      'other_nih_unit_flag': any(OTHER_UNIT.search(s) for s in a['affiliations']),
                      'references': refs[:8],
                      'review_needed': 'Verify identity and DCEG appointment during this work, including moves and joint appointments. A later DCEG coauthor or current branch annotation alone does not establish eligibility.'})
    leads.sort(key=lambda r: (r['other_nih_unit_flag'], not bool(r['author_branch_annotation']), not any(x['orcid_match'] for x in r['references']), -len(r['references']), r['id']))
    return leads


def main():
    rows = i.load()
    leads = audit(rows)
    (c.EXPORT / 'membership_review_candidates.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in leads))
    c.write_csv(c.EXPORT / 'membership_review_candidates.csv', [{
        **{k: r[k] for k in ['id', 'title', 'first_author', 'publication_date', 'status', 'reason', 'lead_type', 'other_nih_unit_flag']},
        'author_branch_annotation': '; '.join(r['author_branch_annotation']),
        'reported_affiliations': '; '.join(r['reported_affiliations']),
        'references_json': json.dumps(r['references'], ensure_ascii=False),
        'review_needed': r['review_needed']} for r in leads])
    summary = {'candidate_publications': len(leads), 'lead_types': dict(Counter(r['lead_type'] for r in leads)),
               'status_counts': dict(Counter(r['status'] for r in leads)),
               'other_nih_unit_flags': sum(r['other_nih_unit_flag'] for r in leads),
               'eligibility_changes': 0,
               'caution': 'These are investigation leads, not an estimate of missed eligible publications.'}
    (c.EXPORT / 'membership_review_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
