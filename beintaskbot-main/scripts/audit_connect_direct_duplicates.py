"""Read-only inventory of historical, provider-confirmed direct JID/LID pairs.

No data is changed.  A row is a merge *candidate*, not permission to merge:
operators must explicitly review any pair with more than one existing route.
"""
import json
import os
import sys

import psycopg
from psycopg.rows import dict_row


SQL = '''
WITH evidence AS (
  SELECT m.tenant_id,substring(m.channel FROM 9) AS channel_id,
    m.raw->'directIdentity'->>'jid' AS jid,m.raw->'directIdentity'->>'lid' AS lid
  FROM saas_crm_messages m
  WHERE m.channel LIKE 'connect:%' AND m.raw ? 'directIdentity'
), verified AS (
  SELECT * FROM evidence WHERE jid ~ '^[1-9][0-9]{5,22}@(s\\.whatsapp\\.net|c\\.us)$'
    AND lid ~ '^[1-9][0-9]{5,22}@lid$'
), pairs AS (
  SELECT tenant_id,channel_id,jid,lid,count(*) AS events FROM verified GROUP BY 1,2,3,4
), routes AS (
  SELECT p.*,array_agg(c.conversation_id ORDER BY c.created_at) FILTER(WHERE c.conversation_id IN (p.jid,p.lid)) AS conversations
  FROM pairs p LEFT JOIN saas_connect_conversations c ON c.tenant_id=p.tenant_id
    AND c.connect_channel_id::text=p.channel_id AND c.conversation_id IN (p.jid,p.lid)
  GROUP BY p.tenant_id,p.channel_id,p.jid,p.lid,p.events
)
SELECT CASE WHEN cardinality(conversations) > 1 THEN 'duplicate_requires_review'
            WHEN cardinality(conversations) = 1 THEN 'mapping_only'
            ELSE 'no_route_yet' END AS state,
       count(*) AS pairs,sum(events) AS evidence_events
FROM routes GROUP BY 1 ORDER BY 1'''


def audit(conn):
    with conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("SET LOCAL statement_timeout='15s'")
        cur.execute(SQL)
        rows=[dict(row) for row in cur.fetchall()]
    return {'historical_direct_identity_pairs':rows,
            'automatic_merge':False,
            'note':'duplicate_requires_review rows are intentionally quarantined; this audit never rewrites history.'}


def main():
    dsn=os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL')
    if not dsn:
        print(json.dumps({'error':'database_connection_not_available'})); return 3
    try:
        with psycopg.connect(dsn,connect_timeout=10,row_factory=dict_row) as conn:
            print(json.dumps(audit(conn)))
        return 0
    except Exception as exc:
        print(json.dumps({'error':'audit_failed','error_type':type(exc).__name__})); return 3


if __name__=='__main__': sys.exit(main())
