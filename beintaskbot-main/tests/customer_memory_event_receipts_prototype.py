"""Isolated event/checkpoint experiment; never imported by the application.

This module requires an explicit loopback test guard, does not register any
writer hook or worker, and does not access AI. The caller must independently
verify that the database instance is disposable; loopback alone does not prove it. It models opaque source versions,
not a verified identity/ACL, a source fingerprint implementation, or memory
updates. Callers must use an explicit disposable loopback database and a fresh
synthetic schema. All methods operate on the caller's transaction.
"""
import os
import re
import uuid
from datetime import datetime

from psycopg import sql

_SCHEMA = re.compile(r'^synthetic_memory_events_[0-9a-f]{32}$')
_KINDS = {'case','crm','message','media'}


def _table(cur, schema, name):
    if (os.environ.get('CUSTOMER_MEMORY_TEST_ISOLATED')!='1'
            or cur.connection.info.dbname!='customer_memory_test'
            or cur.connection.info.host not in {'127.0.0.1','::1','localhost'}
            or cur.connection.info.hostaddr not in {'127.0.0.1','::1'}
            or not isinstance(schema,str) or not _SCHEMA.fullmatch(schema)):
        raise RuntimeError('Synthetic local database required')
    return sql.SQL('{}.{}').format(sql.Identifier(schema),sql.Identifier(name))


def _id(value):
    return str(uuid.UUID(str(value)))


def create_test_schema(cur,schema):
    queue=_table(cur,schema,'queue');events=_table(cur,schema,'events')
    cur.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    cur.execute(sql.SQL('''CREATE TABLE {} (
      tenant UUID NOT NULL, scope UUID NOT NULL, paused BOOLEAN NOT NULL DEFAULT TRUE,
      state TEXT NOT NULL DEFAULT 'idle' CHECK(state IN ('idle','running','blocked')),
      cursor_seq BIGINT NOT NULL DEFAULT 0, generation BIGINT NOT NULL DEFAULT 0,
      due_at TIMESTAMPTZ, claim_token UUID, claim_end BIGINT, lease_until TIMESTAMPTZ,
      PRIMARY KEY(tenant,scope))''').format(queue))
    cur.execute(sql.SQL('''CREATE TABLE {} (
      seq BIGSERIAL PRIMARY KEY, tenant UUID NOT NULL, scope UUID NOT NULL,
      source_kind TEXT NOT NULL, source_id UUID NOT NULL, source_version UUID NOT NULL,
      occurred_at TIMESTAMPTZ NOT NULL, ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(tenant,scope,source_kind,source_id,source_version),
      FOREIGN KEY(tenant,scope) REFERENCES {}(tenant,scope) ON DELETE CASCADE)''').format(events,queue))
    cur.execute(sql.SQL('CREATE INDEX ON {} (tenant,scope,seq)').format(events))


def _scope(cur,schema,tenant,scope):
    table=_table(cur,schema,'queue');tenant=_id(tenant);scope=_id(scope)
    cur.execute(sql.SQL('INSERT INTO {} (tenant,scope) VALUES(%s,%s) ON CONFLICT DO NOTHING').format(table),(tenant,scope))
    cur.execute(sql.SQL('SELECT * FROM {} WHERE tenant=%s AND scope=%s FOR UPDATE').format(table),(tenant,scope))
    return table,tenant,scope,cur.fetchone()


def enqueue(cur,schema,*,tenant,scope,source_kind,source_id,source_version,occurred_at,debounce_seconds=1):
    """Record one opaque version in the caller transaction, not a fact or AI request.

    Scope row lock precedes sequence allocation. Concurrent commits for one
    scope therefore cannot put a later sequence before an uncommitted earlier
    event. Rollback may leave safe sequence gaps. No identity is inferred.
    """
    if (source_kind not in _KINDS or not isinstance(occurred_at,datetime)
            or occurred_at.utcoffset() is None or type(debounce_seconds) is not int
            or not 0<=debounce_seconds<=120):raise ValueError('Invalid synthetic event')
    source_id=_id(source_id);source_version=_id(source_version)
    table,tenant,scope,_=_scope(cur,schema,tenant,scope)
    events=_table(cur,schema,'events')
    cur.execute(sql.SQL('''INSERT INTO {}(tenant,scope,source_kind,source_id,source_version,occurred_at)
      VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant,scope,source_kind,source_id,source_version) DO NOTHING
      RETURNING seq''').format(events),(tenant,scope,source_kind,source_id,source_version,occurred_at))
    row=cur.fetchone()
    if not row:
        cur.execute(sql.SQL('''SELECT occurred_at FROM {} WHERE tenant=%s AND scope=%s AND source_kind=%s
          AND source_id=%s AND source_version=%s''').format(events),(tenant,scope,source_kind,source_id,source_version))
        prior=cur.fetchone()
        if not prior or prior['occurred_at']!=occurred_at:raise ValueError('Conflicting synthetic source version')
        return None
    cur.execute(sql.SQL('''UPDATE {} SET generation=generation+1,
      due_at=LEAST(COALESCE(due_at,clock_timestamp()+(%s * interval '1 second')),
                   clock_timestamp()+(%s * interval '1 second')) WHERE tenant=%s AND scope=%s''').format(table),
      (debounce_seconds,debounce_seconds,tenant,scope))
    return int(row['seq'])


def pause(cur,schema,*,tenant,scope,paused):
    if type(paused) is not bool:raise ValueError('Boolean required')
    table,tenant,scope,_=_scope(cur,schema,tenant,scope)
    # An interrupted in-flight batch is quarantined. Resuming the scope alone
    # must never replay an attempt whose external side effects are unknown.
    cur.execute(sql.SQL('''UPDATE {} SET paused=%s,
      state=CASE WHEN %s AND state='running' THEN 'blocked' ELSE state END,
      claim_token=CASE WHEN %s AND state='running' THEN NULL ELSE claim_token END,
      claim_end=CASE WHEN %s AND state='running' THEN NULL ELSE claim_end END,
      lease_until=CASE WHEN %s AND state='running' THEN NULL ELSE lease_until END
      WHERE tenant=%s AND scope=%s''').format(table),(paused,paused,paused,paused,paused,tenant,scope))


def claim(cur,schema,*,tenant,scope,batch_size=10,lease_seconds=30):
    """Claim a bounded receipt batch; expiry blocks instead of retrying unknown work."""
    if (type(batch_size) is not int or not 1<=batch_size<=10 or type(lease_seconds) is not int
            or not 1<=lease_seconds<=120):raise ValueError('Invalid synthetic batch')
    table,tenant,scope,row=_scope(cur,schema,tenant,scope)
    if row['paused'] or row['state']=='blocked':return None
    if row['state']=='running':
        cur.execute(sql.SQL('''UPDATE {} SET state='blocked',claim_token=NULL,claim_end=NULL,lease_until=NULL
          WHERE tenant=%s AND scope=%s AND lease_until<clock_timestamp()''').format(table),(tenant,scope))
        return None
    cur.execute(sql.SQL('SELECT (due_at IS NOT NULL AND due_at<=clock_timestamp()) AS due FROM {} WHERE tenant=%s AND scope=%s').format(table),(tenant,scope))
    if not cur.fetchone()['due']:return None
    events=_table(cur,schema,'events')
    cur.execute(sql.SQL('''SELECT seq,source_kind,source_id,source_version,occurred_at FROM {}
      WHERE tenant=%s AND scope=%s AND seq>%s ORDER BY seq LIMIT %s''').format(events),
      (tenant,scope,row['cursor_seq'],batch_size))
    batch=cur.fetchall()
    if not batch:return None
    token=str(uuid.uuid4());end=int(batch[-1]['seq'])
    cur.execute(sql.SQL('''UPDATE {} SET state='running',claim_token=%s,claim_end=%s,
      lease_until=clock_timestamp()+(%s * interval '1 second') WHERE tenant=%s AND scope=%s''').format(table),
      (token,end,lease_seconds,tenant,scope))
    return {'token':token,'generation':int(row['generation']),'events':batch}


def finish(cur,schema,*,tenant,scope,token,debounce_seconds=1):
    """Commit only the claimed contiguous checkpoint; stale or expired work fails."""
    if type(debounce_seconds) is not int or not 0<=debounce_seconds<=120:raise ValueError('Invalid debounce')
    table,tenant,scope,row=_scope(cur,schema,tenant,scope);token=_id(token)
    if row['paused'] or row['state']!='running' or str(row['claim_token'])!=token:return False
    events=_table(cur,schema,'events')
    cur.execute(sql.SQL('''UPDATE {} SET cursor_seq=claim_end,state='idle',claim_token=NULL,
      claim_end=NULL,lease_until=NULL,due_at=CASE WHEN EXISTS(SELECT 1 FROM {} e
        WHERE e.tenant=%s AND e.scope=%s AND e.seq>claim_end)
        THEN clock_timestamp()+(%s * interval '1 second') ELSE NULL END
      WHERE tenant=%s AND scope=%s AND lease_until>=clock_timestamp() RETURNING cursor_seq''').format(table,events),
      (tenant,scope,debounce_seconds,tenant,scope))
    return bool(cur.fetchone())
