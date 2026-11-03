"""Local-only microbenchmark, not a production performance guarantee."""
from pathlib import Path
from statistics import median
import json
import os
import time
from urllib.parse import urlsplit
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from ledger import local_connect,reject_libpq_env
import tenant_ai_spend_budget as spend

reject_libpq_env()
dsn=os.environ.get('VOICE_BUDGET_POC_ADMIN_URL','');p=urlsplit(dsn);pg=conninfo_to_dict(dsn)
if (p.scheme not in ('postgres','postgresql') or p.query or p.fragment or p.path!='/postgres' or
    p.hostname not in ('localhost','127.0.0.1','::1') or pg.get('host') not in ('localhost','127.0.0.1','::1')):
    raise RuntimeError('localhost_admin_only')
name='test_voice_budget_'+uuid.uuid4().hex
db=p._replace(path='/'+name).geturl()
root=Path(__file__).resolve().parents[1]
with psycopg.connect(dsn,hostaddr='::1' if p.hostname=='::1' else '127.0.0.1',autocommit=True) as admin:
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        with local_connect(db) as conn:
            spend.ensure_schema(conn)
            conn.execute((root/'voice_budget_poc'/'voice_test_grant.sql').read_text())
            conn.execute('''INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,state)
              SELECT %s,md5(i::text)::uuid,'nonvoice-'||i,'synthetic','ordinary',1,'reserved' FROM generate_series(1,5000) AS i''',(str(uuid.uuid4()),))
        def measure():
            samples=[]
            for _ in range(9):
                with local_connect(db) as conn:
                    start=time.perf_counter();conn.execute("UPDATE saas_ai_spend_reservations SET completed_at=completed_at WHERE kind='ordinary'")
                    elapsed=(time.perf_counter()-start)*1000;conn.rollback()
                samples.append(elapsed)
            return samples[2:]  # warm-up excluded
        before=measure()
        with local_connect(db) as conn:
            conn.execute((root/'voice_budget_poc'/'migration_dispatch_v2.sql').read_text())
            conn.execute((Path(__file__).parent/'provenance.sql').read_text())
        after=measure()
        print(json.dumps({'rows':5000,'samples':7,'baseline_median_ms':round(median(before),2),
                          'conditional_trigger_median_ms':round(median(after),2),
                          'ratio':round(median(after)/median(before),3)}))
    finally:admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
