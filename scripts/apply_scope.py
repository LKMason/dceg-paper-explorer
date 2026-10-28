#!/usr/bin/env python3
"""Apply reversible publication-type policy to the existing evidence database."""
import json
import sqlite3
import collect as c

db=sqlite3.connect(c.DB)
for key,record_json in db.execute('SELECT id,record_json FROM publications').fetchall():
    r=json.loads(record_json)
    r['status'],r['reason']=c.apply_publication_scope(r,r['status'],r['reason'])
    db.execute('UPDATE publications SET status=?,reason=?,record_json=? WHERE id=?',
               (r['status'],r['reason'],json.dumps(r,ensure_ascii=False),key))
policy=json.loads((c.ROOT/'config'/'publication_scope.json').read_text())
db.execute('INSERT OR REPLACE INTO collection_metadata VALUES(?,?)',('publication_scope',json.dumps(policy)))
db.commit()
assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
db.close()
print('Applied publication scope without discarding author or eligibility evidence.')
