"""Read-only deployment gate. Never initializes schema or prints credentials/JIDs.
Run from repository app directory with DATABASE_URL or DATABASE_PUBLIC_URL set.
Exit 0: no detected ambiguity; 2: blocked; 3: no verified database audit.
"""
import json
import os
import sys
import psycopg
from psycopg.rows import dict_row


def audit(conn):
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout='10s'")
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='saas_ai_conversation_state' AND column_name='channel_id'")
        migrated=bool(cur.fetchone())
        legacy=" AND cs.channel_id=''" if migrated else ''
        cur.execute("""SELECT count(*) AS unresolved FROM saas_ai_conversation_state cs
          WHERE cs.provider='connect' """+legacy+""" AND NOT EXISTS (
            SELECT 1 FROM saas_connect_conversations c
            WHERE c.tenant_id=cs.tenant_id AND c.conversation_id=cs.conversation_id
              AND NOT EXISTS (SELECT 1 FROM saas_connect_conversations other
                WHERE other.tenant_id=c.tenant_id AND other.conversation_id=c.conversation_id
                  AND other.connect_channel_id<>c.connect_channel_id)
              AND NOT EXISTS (SELECT 1 FROM saas_ai_agent_jobs j
                WHERE j.tenant_id=c.tenant_id AND j.provider='connect' AND j.conversation_id=c.conversation_id
                  AND (j.channel_id<>c.connect_channel_id::text OR j.lead_id<>c.lead_id))
              AND NOT EXISTS (SELECT 1 FROM saas_tenant_webhook_events e
                WHERE e.tenant_id=c.tenant_id AND e.provider='connect' AND e.payload->>'conversation_id'=c.conversation_id
                  AND COALESCE(e.payload->>'channel_id','')<>c.connect_channel_id::text))""")
        unresolved=int(cur.fetchone()['unresolved'])
        if migrated:
            # A retained quarantine must be resolved explicitly, even if a route was later removed.
            cur.execute("SELECT count(*) AS n FROM saas_ai_conversation_state WHERE provider='connect' AND channel_id=''")
            unresolved=max(unresolved,int(cur.fetchone()['n']))
        cur.execute("""SELECT count(*) AS n FROM saas_ai_agent_jobs j WHERE j.provider='connect' AND (
          NOT EXISTS (SELECT 1 FROM saas_connect_conversations c WHERE c.tenant_id=j.tenant_id
            AND c.connect_channel_id::text=j.channel_id AND c.conversation_id=j.conversation_id AND c.lead_id=j.lead_id)
          OR NOT EXISTS (SELECT 1 FROM saas_tenant_webhook_events e WHERE e.tenant_id=j.tenant_id
            AND e.fingerprint=j.event_fingerprint AND e.provider='connect'
            AND e.payload->>'channel_id'=j.channel_id AND e.payload->>'conversation_id'=j.conversation_id))""")
        inconsistent=int(cur.fetchone()['n'])
        return {'schema_channel_column':migrated,'unresolved_legacy_states':unresolved,
                'inconsistent_job_routes':inconsistent,'safe_to_migrate':not (unresolved or inconsistent)}


def main():
    dsn=os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL')
    if not dsn:
        print(json.dumps({'safe_to_migrate':False,'audit':'database_connection_not_available'}));return 3
    try:
        with psycopg.connect(dsn,connect_timeout=10,row_factory=dict_row) as conn:result=audit(conn)
        print(json.dumps(result));return 0 if result['safe_to_migrate'] else 2
    except Exception as error:
        # Never echo libpq DSNs or raw errors containing host/account details.
        print(json.dumps({'safe_to_migrate':False,'audit':'failed','error_type':type(error).__name__}));return 3

if __name__=='__main__':sys.exit(main())
