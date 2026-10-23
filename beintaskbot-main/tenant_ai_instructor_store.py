"""Tenant-scoped AI Instructor connections, jobs, audit events and private artifacts.

The queue is opt-in and stores only encrypted connection credentials. This module
does not start workers, call AI providers, publish lessons or perform ERP writes.
"""
from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import uuid
from pathlib import Path

from tenant_platform import _connect, _fernet, _json
from tenant_ai_agents_store import _authorize, _audit
from tenant_linear_policy import TenantLinearError, identifier

MAX_CREDENTIAL_BYTES = 8192
MAX_JOB_TOKENS = 12000
MAX_AI_CALLS_PER_JOB = 16
MAX_JOB_ACTIONS = 20
MAX_JOB_SECONDS = 900
_JOB_STATES = {'queued', 'running', 'blocked', 'completed', 'failed', 'cancelled'}


def _feature_enabled():
    return str(os.environ.get('AI_INSTRUCTOR_ENABLED') or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _artifact_root():
    raw=str(os.environ.get('AI_INSTRUCTOR_ARTIFACT_DIR') or '').strip()
    if not raw:raise TenantLinearError('Private artifact storage is not configured',503)
    root=Path(raw).resolve();public=(Path(__file__).resolve().parent/'docs').resolve()
    try:root.relative_to(public)
    except ValueError:return root
    raise TenantLinearError('Artifact storage cannot be inside public assets',503)


def schema(conn):
    """Idempotent additive schema; historical lesson and customer tables untouched."""
    from tenant_ai_agents_schema import ensure_schema
    ensure_schema(conn)
    with conn.cursor() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS saas_ai_instructor_connections(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          id UUID NOT NULL, label TEXT NOT NULL, origin TEXT NOT NULL,
          account_ref UUID, demo_company_id UUID,
          attestation_ref UUID, restore_receipt_ref UUID,
          credentials BYTEA NOT NULL, enabled BOOLEAN NOT NULL DEFAULT FALSE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,origin,account_ref))''')
        for column in ('account_ref','demo_company_id','attestation_ref','restore_receipt_ref'):
            c.execute(f'ALTER TABLE saas_ai_instructor_connections ALTER COLUMN {column} DROP NOT NULL')
        c.execute('''CREATE TABLE IF NOT EXISTS saas_ai_instructor_jobs(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          id UUID NOT NULL, actor BIGINT NOT NULL, connection_id UUID NOT NULL,
          primary_agent_id UUID NOT NULL, expert_agent_id UUID NOT NULL,
          idempotency_key TEXT NOT NULL, title TEXT NOT NULL, language TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued', progress JSONB NOT NULL DEFAULT '{}'::jsonb,
          budget_tokens INT NOT NULL DEFAULT 12000, tokens_used INT NOT NULL DEFAULT 0,reserved_tokens INT NOT NULL DEFAULT 0,
          max_actions INT NOT NULL DEFAULT 20, actions_used INT NOT NULL DEFAULT 0,
          deadline_at TIMESTAMPTZ NOT NULL, lease_until TIMESTAMPTZ, lesson_id UUID,
          error_code TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,idempotency_key),
          FOREIGN KEY(tenant_id,connection_id) REFERENCES saas_ai_instructor_connections(tenant_id,id))''')
        c.execute('ALTER TABLE saas_ai_instructor_jobs ADD COLUMN IF NOT EXISTS reserved_tokens INT NOT NULL DEFAULT 0')
        c.execute('ALTER TABLE saas_ai_instructor_jobs ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ')
        c.execute('ALTER TABLE saas_ai_instructor_jobs ADD COLUMN IF NOT EXISTS ai_calls_used INT NOT NULL DEFAULT 0')
        c.execute('ALTER TABLE saas_ai_instructor_jobs ADD COLUMN IF NOT EXISTS ai_calls_max INT NOT NULL DEFAULT 16')
        c.execute("ALTER TABLE saas_ai_instructor_jobs ADD COLUMN IF NOT EXISTS permission_level TEXT NOT NULL DEFAULT 'demonstration_operation'")
        c.execute("SELECT 1 FROM pg_constraint WHERE conrelid='saas_ai_instructor_jobs'::regclass AND conname='saas_ai_instructor_jobs_permission_level_check'")
        if not c.fetchone():
            c.execute("ALTER TABLE saas_ai_instructor_jobs ADD CONSTRAINT saas_ai_instructor_jobs_permission_level_check CHECK(permission_level IN ('research','demonstration_operation'))")
        c.execute('''CREATE TABLE IF NOT EXISTS saas_ai_instructor_restore_claims(
          tenant_id UUID NOT NULL, restore_receipt_ref UUID NOT NULL, connection_id UUID NOT NULL,
          job_id UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,restore_receipt_ref), UNIQUE(tenant_id,job_id),
          FOREIGN KEY(tenant_id,connection_id) REFERENCES saas_ai_instructor_connections(tenant_id,id),
          FOREIGN KEY(tenant_id,job_id) REFERENCES saas_ai_instructor_jobs(tenant_id,id))''')
        c.execute('''CREATE INDEX IF NOT EXISTS saas_ai_instructor_jobs_queue_idx
          ON saas_ai_instructor_jobs(status,created_at) WHERE status='queued' ''')
        c.execute('''CREATE TABLE IF NOT EXISTS saas_ai_instructor_events(
          tenant_id UUID NOT NULL, job_id UUID NOT NULL, seq BIGINT GENERATED ALWAYS AS IDENTITY,
          event_type TEXT NOT NULL, detail JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,job_id,seq),
          FOREIGN KEY(tenant_id,job_id) REFERENCES saas_ai_instructor_jobs(tenant_id,id) ON DELETE CASCADE)''')
        c.execute('''CREATE TABLE IF NOT EXISTS saas_ai_instructor_token_reservations(
          tenant_id UUID NOT NULL,job_id UUID NOT NULL,id UUID NOT NULL,reserved_tokens INT NOT NULL,
          used_tokens INT NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'reserved',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),settled_at TIMESTAMPTZ,
          PRIMARY KEY(tenant_id,job_id,id),
          FOREIGN KEY(tenant_id,job_id) REFERENCES saas_ai_instructor_jobs(tenant_id,id) ON DELETE CASCADE,
          CHECK(status IN ('reserved','settled','unknown')))''')
        c.execute('''CREATE TABLE IF NOT EXISTS saas_ai_instructor_artifacts(
          tenant_id UUID NOT NULL, job_id UUID NOT NULL, id UUID NOT NULL, kind TEXT NOT NULL,
          relpath TEXT NOT NULL, mime TEXT NOT NULL, size_bytes BIGINT NOT NULL,
          sha256 TEXT NOT NULL, sanitization_receipt_ref UUID NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,id),
          FOREIGN KEY(tenant_id,job_id) REFERENCES saas_ai_instructor_jobs(tenant_id,id) ON DELETE CASCADE,
          CHECK(kind IN ('screenshot','recording','subtitle','scenario')))''')
        c.execute("SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid='saas_ai_instructor_artifacts'::regclass AND conname='saas_ai_instructor_artifacts_kind_check'")
        check=c.fetchone()
        if check and 'source_recording' not in str(check.get('definition') or ''):
            c.execute('ALTER TABLE saas_ai_instructor_artifacts DROP CONSTRAINT saas_ai_instructor_artifacts_kind_check')
            c.execute("ALTER TABLE saas_ai_instructor_artifacts ADD CONSTRAINT saas_ai_instructor_artifacts_kind_check CHECK(kind IN ('screenshot','recording','source_recording','subtitle','scenario'))")
        elif not check:
            c.execute("ALTER TABLE saas_ai_instructor_artifacts ADD CONSTRAINT saas_ai_instructor_artifacts_kind_check CHECK(kind IN ('screenshot','recording','source_recording','subtitle','scenario'))")


def _connection_dict(row):
    if not row:
        return None
    result={k: row[k] for k in ('id','label','origin','enabled','updated_at')}
    result['id']=str(result['id'])
    if hasattr(result.get('updated_at'),'isoformat'):result['updated_at']=result['updated_at'].isoformat()
    checks=(('account_ref','account_identity_unverified'),
            ('demo_company_id','demo_company_unverified'),
            ('attestation_ref','isolation_scope_unverified'))
    result['research_ready']=bool(row.get('enabled') is True and all(row.get(field) for field,_ in checks))
    result['operation_ready']=bool(result['research_ready'] and row.get('restore_receipt_ref'))
    if result['research_ready'] and not row.get('restore_receipt_ref'):
        result['operation_blocker']='fixture_restore_unverified'
    result['verification_blockers']=[reason for field,reason in checks if not row.get(field)]
    if row.get('enabled') is not True:
        result['verification_blockers'].append('connection_not_activated')
    result['verification_required']=bool(result['verification_blockers'])
    return result


def save_connection(profile, data):
    """Save an owner-provided test login encrypted; never return credential material."""
    if not isinstance(data, dict) or set(data) - {'id','label','origin','account_ref','demo_company_id',
        'attestation_ref','restore_receipt_ref','username','password','enabled'}:
        raise ValueError('invalid_connection')
    from tenant_ai_instructor_contract import DemoConnection, PERMISSIONS
    from tenant_ai_instructor_browser import _allowed_origin
    tenant = identifier(profile.get('tenant_id'))
    connection_id = identifier(data.get('id') or uuid.uuid4())
    label = str(data.get('label') or '').strip()[:100]
    origin = str(data.get('origin') or '').strip().rstrip('/')
    proof_fields=('account_ref','demo_company_id','attestation_ref','restore_receipt_ref')
    if any(data.get(k) not in (None,'') for k in proof_fields):
        raise ValueError('verification_proofs_server_only')
    present=[data.get(k) not in (None,'') for k in proof_fields]
    if any(present) and not all(present): raise ValueError('incomplete_verification_proof')
    proof={k:identifier(data[k]) for k in proof_fields} if all(present) else {}
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '')
    if not label or not username or not password or len(username) > 240 or len(password) > 1000:
        raise ValueError('credential_required')
    secret = _json({'tenant_id': tenant, 'account_ref': proof.get('account_ref'), 'username': username,
                    'password': password}).encode()
    if len(secret) > MAX_CREDENTIAL_BYTES:
        raise ValueError('credential_size')
    # A credential can be staged without claiming company isolation or fixture
    # restoration. Verified references must come from a separate reviewed process.
    contract = DemoConnection(tenant, proof.get('demo_company_id',str(uuid.uuid4())),str(uuid.uuid4()),origin,
                              proof.get('account_ref',str(uuid.uuid4())),proof.get('attestation_ref',str(uuid.uuid4())),
                              proof.get('restore_receipt_ref',str(uuid.uuid4())),PERMISSIONS,True)
    contract.validate()
    if not _allowed_origin(origin): raise TenantLinearError('Demo program origin is not on the platform allowlist',403)
    enabled = data.get('enabled', False)
    if enabled is not False:
        raise ValueError('connection_requires_verified_activation')
    cipher = _fernet().encrypt(secret)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            current_tenant, actor = _authorize(c, profile)
            if current_tenant != tenant:
                raise TenantLinearError('Tenant mismatch', 403)
            c.execute('''INSERT INTO saas_ai_instructor_connections
              (tenant_id,id,label,origin,account_ref,demo_company_id,attestation_ref,restore_receipt_ref,credentials,enabled)
              VALUES(%s::uuid,%s::uuid,%s,%s,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,FALSE)
              ON CONFLICT(tenant_id,id) DO UPDATE SET label=EXCLUDED.label,origin=EXCLUDED.origin,
                account_ref=EXCLUDED.account_ref,demo_company_id=EXCLUDED.demo_company_id,
                attestation_ref=EXCLUDED.attestation_ref,restore_receipt_ref=EXCLUDED.restore_receipt_ref,
                credentials=EXCLUDED.credentials,enabled=FALSE,updated_at=now()
              RETURNING id,label,origin,account_ref,demo_company_id,attestation_ref,restore_receipt_ref,enabled,updated_at''',
              (tenant,connection_id,label,origin,proof.get('account_ref'),proof.get('demo_company_id'),
               proof.get('attestation_ref'),proof.get('restore_receipt_ref'),cipher))
            row = c.fetchone()
            _audit(c,tenant,actor,'ai_instructor_connection_saved','ai_instructor_connection',connection_id,
                   {'enabled':False})
        conn.commit()
    return _connection_dict(row)


def verify_connection(profile, connection_id):
    """Verify login and company via server-configured ERP identity response.

    No company/account proof is accepted from the browser. A verified connection
    becomes read-only research-ready; a fixture restore receipt remains a
    separate prerequisite for data-changing demonstration operations.
    """
    tenant=identifier(profile.get('tenant_id'));connection_id=identifier(connection_id)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            current_tenant,actor=_authorize(c,profile)
            if current_tenant!=tenant:raise TenantLinearError('Tenant mismatch',403)
            c.execute('SELECT id,origin,credentials FROM saas_ai_instructor_connections WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,connection_id))
            row=c.fetchone()
            if not row:raise TenantLinearError('Demo connection not found',404)
            origin=row['origin'];cipher=bytes(row['credentials'])
        conn.commit()
    try:credentials=json.loads(_fernet().decrypt(cipher).decode('utf-8'))
    except Exception:raise TenantLinearError('Stored demo credentials are unavailable',409) from None
    if credentials.get('tenant_id')!=tenant:
        credentials.clear();raise TenantLinearError('Credential tenant mismatch',403)
    try:
        import asyncio
        from tenant_ai_instructor_browser import PlaywrightDemoExecutor
        proof=asyncio.run(PlaywrightDemoExecutor().verify_identity(origin,credentials))
    finally:credentials.clear()
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            current_tenant,actor=_authorize(c,profile)
            if current_tenant!=tenant:raise TenantLinearError('Tenant mismatch',403)
            c.execute('''UPDATE saas_ai_instructor_connections SET account_ref=%s::uuid,demo_company_id=%s::uuid,
              attestation_ref=%s::uuid,enabled=TRUE,updated_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND origin=%s AND credentials=%s
              RETURNING id,label,origin,account_ref,demo_company_id,attestation_ref,restore_receipt_ref,enabled,updated_at''',
              (proof['account_ref'],proof['demo_company_id'],proof['attestation_ref'],tenant,connection_id,origin,cipher))
            verified=c.fetchone()
            if not verified:raise TenantLinearError('Connection changed during verification; retry safely',409)
            _audit(c,tenant,actor,'ai_instructor_connection_verified','ai_instructor_connection',connection_id,
                   {'company_identity_verified':True,'isolation_attestation_verified':True,'restore_receipt_verified':False})
        conn.commit()
    return _connection_dict(verified)


def list_connections(profile):
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            tenant, _actor = _authorize(c, profile)
            c.execute('''SELECT id,label,origin,account_ref,demo_company_id,attestation_ref,restore_receipt_ref,enabled,updated_at
              FROM saas_ai_instructor_connections WHERE tenant_id=%s::uuid ORDER BY updated_at DESC LIMIT 50''',(tenant,))
            return {'enabled':_feature_enabled(),'connections':[_connection_dict(r) for r in c.fetchall()],
                    'real_pilot':'unverified','external_effects_allowed':False}


def enqueue(profile, data):
    """Queue an explicit user action. Does not itself start or call an executor/AI."""
    if not _feature_enabled():
        raise TenantLinearError('AI instructor is disabled by server configuration', 409)
    if not isinstance(data,dict) or set(data)-{'connection_id','primary_agent_id','expert_agent_id',
        'idempotency_key','title','language','budget_tokens','permission_level'}:
        raise ValueError('invalid_job')
    permission_level=data.get('permission_level','research')
    if permission_level not in {'research','demonstration_operation'}:raise ValueError('permission_level_invalid')
    tenant=identifier(profile.get('tenant_id'));connection_id=identifier(data.get('connection_id'))
    primary=identifier(data.get('primary_agent_id'));expert=identifier(data.get('expert_agent_id'))
    idem=str(data.get('idempotency_key') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9._:-]{16,128}',idem): raise ValueError('idempotency_key')
    title=str(data.get('title') or '').strip()[:180]
    language=data.get('language')
    if not title or language not in {'ru','az','en'}: raise ValueError('job_title_or_language')
    budget=data.get('budget_tokens',MAX_JOB_TOKENS)
    if type(budget) is not int or not 1<=budget<=MAX_JOB_TOKENS: raise ValueError('token_budget')
    job_id=uuid.uuid4()
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            authorized_tenant,actor=_authorize(c,profile)
            if authorized_tenant!=tenant: raise TenantLinearError('Tenant mismatch',403)
            c.execute('''SELECT id,account_ref,demo_company_id,attestation_ref,restore_receipt_ref,enabled FROM saas_ai_instructor_connections
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND enabled=TRUE FOR SHARE''',(tenant,connection_id))
            verified_connection=c.fetchone()
            if (not verified_connection or not all(verified_connection.get(k) for k in ('account_ref','demo_company_id','attestation_ref'))):
                raise TenantLinearError('No server-verified demo connection is available',409)
            if permission_level=='demonstration_operation' and not verified_connection.get('restore_receipt_ref'):
                raise TenantLinearError('A verified fixture restore receipt is required for a demonstration operation',409)
            c.execute("""SELECT id,role,enabled,mode,scopes,expert_policy FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[])
              ORDER BY role""",(tenant,[primary,expert]))
            agents={str(r['id']):r for r in c.fetchall()}
            if (len(agents)!=2 or agents.get(primary,{}).get('role')!='primary' or
                agents.get(expert,{}).get('role')!='expert' or not all(r['enabled'] for r in agents.values()) or
                not all(r.get('mode')=='manual' for r in agents.values()) or
                any(r.get('scopes') for r in agents.values())):
                raise TenantLinearError('An enabled tenant Primary and Expert are required',409)
            from tenant_ai_expert_policy import helper_ids, policy as expert_policy
            if expert not in helper_ids(expert_policy(agents[primary].get('expert_policy') or {})):
                raise TenantLinearError('The Expert is not authorized for this Primary',409)
            c.execute('''INSERT INTO saas_ai_instructor_jobs
              (tenant_id,id,actor,connection_id,primary_agent_id,expert_agent_id,idempotency_key,title,language,permission_level,
               budget_tokens,max_actions,ai_calls_max,deadline_at)
              VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,now()+(%s * interval '1 second'))
              ON CONFLICT(tenant_id,idempotency_key) DO NOTHING
              RETURNING id,status,created_at''',(tenant,job_id,actor,connection_id,primary,expert,idem,title,
                    language,permission_level,budget,MAX_JOB_ACTIONS,MAX_AI_CALLS_PER_JOB,MAX_JOB_SECONDS))
            row=c.fetchone()
            created=row is not None
            if created and permission_level=='demonstration_operation':
                c.execute('''INSERT INTO saas_ai_instructor_restore_claims(tenant_id,restore_receipt_ref,connection_id,job_id)
                  VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid) ON CONFLICT(tenant_id,restore_receipt_ref) DO NOTHING
                  RETURNING job_id''',(tenant,verified_connection['restore_receipt_ref'],connection_id,row['id']))
                if not c.fetchone():
                    raise TenantLinearError('The verified restore receipt has already been used',409)
            if not row:
                c.execute('SELECT id,status,created_at FROM saas_ai_instructor_jobs WHERE tenant_id=%s::uuid AND idempotency_key=%s',(tenant,idem))
                row=c.fetchone()
            if created:_event(c,tenant,row['id'],'queued',{'status':row['status']})
            _audit(c,tenant,actor,'ai_instructor_job_queued','ai_instructor_job',row['id'],{'status':row['status']})
        conn.commit()
    return {'id':str(row['id']),'status':row['status'],'created_at':row['created_at'].isoformat() if hasattr(row['created_at'],'isoformat') else str(row['created_at'])}


def list_jobs(profile, limit=50):
    limit=min(max(int(limit),1),50)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            tenant,_actor=_authorize(c,profile)
            c.execute('''SELECT id,title,language,permission_level,status,progress,budget_tokens,tokens_used,reserved_tokens,max_actions,actions_used,ai_calls_used,ai_calls_max,
              lesson_id,error_code,created_at,updated_at FROM saas_ai_instructor_jobs
              WHERE tenant_id=%s::uuid ORDER BY created_at DESC LIMIT %s''',(tenant,limit))
            return [_json_row(r) for r in c.fetchall()]


def _json_row(row):
    result=dict(row)
    result['id']=str(result['id'])
    if result.get('lesson_id'): result['lesson_id']=str(result['lesson_id'])
    for key in ('created_at','updated_at'):
        if hasattr(result.get(key),'isoformat'):result[key]=result[key].isoformat()
    return result


def job_detail(profile, job_id):
    job_id=identifier(job_id)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            tenant,_actor=_authorize(c,profile)
            c.execute('SELECT * FROM saas_ai_instructor_jobs WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,job_id))
            row=c.fetchone()
            if not row: raise TenantLinearError('Not found',404)
            c.execute('SELECT seq,event_type,detail,created_at FROM saas_ai_instructor_events WHERE tenant_id=%s::uuid AND job_id=%s::uuid ORDER BY seq',(tenant,job_id))
            events=[_json_row(r) for r in c.fetchall()]
            c.execute('''SELECT id,kind,mime,size_bytes,sha256,created_at FROM saas_ai_instructor_artifacts
              WHERE tenant_id=%s::uuid AND job_id=%s::uuid ORDER BY created_at''',(tenant,job_id))
            artifacts=[{**_json_row(r),'url':f"/api/platform/ai/instructor/jobs/{job_id}/artifacts/{r['id']}"} for r in c.fetchall()]
            view=_json_row(row)
            result={k:view.get(k) for k in ('id','title','language','permission_level','status','progress','budget_tokens','tokens_used','reserved_tokens',
                    'max_actions','actions_used','ai_calls_used','ai_calls_max','lesson_id','error_code','created_at','updated_at')}
            result['events']=events;result['artifacts']=artifacts
            return result


def cancel(profile, job_id):
    job_id=identifier(job_id)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            tenant,actor=_authorize(c,profile)
            c.execute('''UPDATE saas_ai_instructor_jobs SET status='cancelled',lease_until=NULL,updated_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND actor=%s AND status IN ('queued','running')
              RETURNING id''',(tenant,job_id,actor))
            if not c.fetchone(): raise TenantLinearError('Only a queued job owned by you can be cancelled',409)
            _event(c,tenant,job_id,'cancelled',{})
            _audit(c,tenant,actor,'ai_instructor_job_cancelled','ai_instructor_job',job_id,{})
        conn.commit()
    return {'id':job_id,'status':'cancelled'}


def _event(cur,tenant,job_id,event_type,detail):
    if event_type not in {'queued','claimed','connecting','researching','action_started','action_completed','screenshot_captured',
                          'preparing_script','scenario_created','rendering_video','video_rendered','saving_lesson','lesson_saved','blocked','failed','cancelled'}:
        raise ValueError('event_type')
    allowed={'action','target','reason','lesson_id','artifact_id','evidence_ref','status','published','sha256','kind'}
    safe={str(k):str(v)[:300] for k,v in (detail or {}).items() if str(k) in allowed and not isinstance(v,(dict,list))}
    cur.execute('INSERT INTO saas_ai_instructor_events(tenant_id,job_id,event_type,detail) VALUES(%s::uuid,%s::uuid,%s,%s::jsonb)',
                (tenant,job_id,event_type,_json(safe)))


def claim_one():
    """Worker primitive: claim only explicit queue entries; no implicit old-history work."""
    if not _feature_enabled(): return None
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            # A crashed worker is never retried: its last provider/action may have
            # completed remotely. Preserve all outstanding reservations as unknown.
            c.execute("""UPDATE saas_ai_instructor_jobs SET status='blocked',error_code='execution_interrupted_unknown',
              lease_until=NULL,updated_at=now() WHERE status='running' AND lease_until<=now()
              RETURNING tenant_id,id""")
            for interrupted in c.fetchall():
                c.execute("""UPDATE saas_ai_instructor_token_reservations SET status='unknown',
                  used_tokens=reserved_tokens,settled_at=now() WHERE tenant_id=%s::uuid AND job_id=%s::uuid AND status='reserved'""",
                  (interrupted['tenant_id'],interrupted['id']))
                _event(c,interrupted['tenant_id'],interrupted['id'],'blocked',{'reason':'execution_interrupted_unknown'})
            c.execute("UPDATE saas_ai_instructor_jobs SET status='failed',error_code='instructor_queue_timeout',updated_at=now() WHERE status='queued' AND deadline_at<=now() RETURNING tenant_id,id")
            for expired in c.fetchall():_event(c,expired['tenant_id'],expired['id'],'failed',{'reason':'instructor_queue_timeout'})
            c.execute('''SELECT tenant_id,id FROM saas_ai_instructor_jobs
              WHERE status='queued' AND deadline_at>now() ORDER BY created_at
              FOR UPDATE SKIP LOCKED LIMIT 1''')
            item=c.fetchone()
            if not item:
                conn.commit()
                return None
            tenant,job_id=str(item['tenant_id']),str(item['id'])
            c.execute("UPDATE saas_ai_instructor_jobs SET status='running',lease_until=now()+interval '3 minutes',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING *",(tenant,job_id))
            row=c.fetchone();_event(c,tenant,job_id,'claimed',{})
            c.execute('SELECT * FROM saas_ai_instructor_connections WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,row['connection_id']))
            connection=c.fetchone()
            permission_level=row.get('permission_level') or 'demonstration_operation'
            required=('account_ref','demo_company_id','attestation_ref')
            if permission_level=='demonstration_operation':required=(*required,'restore_receipt_ref')
            if (not connection or connection.get('enabled') is not True or
                not all(connection.get(k) for k in required)):
                c.execute("UPDATE saas_ai_instructor_jobs SET status='blocked',error_code='demo_verification_required',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,job_id))
                _event(c,tenant,job_id,'blocked',{'reason':'demo_verification_required'})
                conn.commit();return None
            if permission_level=='demonstration_operation':
                c.execute('''SELECT 1 FROM saas_ai_instructor_restore_claims
                  WHERE tenant_id=%s::uuid AND restore_receipt_ref=%s::uuid AND connection_id=%s::uuid AND job_id=%s::uuid''',
                  (tenant,connection['restore_receipt_ref'],connection['id'],job_id))
                if not c.fetchone():
                    c.execute("UPDATE saas_ai_instructor_jobs SET status='blocked',error_code='fixture_restore_claim_missing',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,job_id))
                    _event(c,tenant,job_id,'blocked',{'reason':'fixture_restore_claim_missing'})
                    conn.commit();return None
            c.execute('''SELECT id,role,enabled,mode,scopes,expert_policy FROM saas_ai_agents
              WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[]) FOR SHARE''',
              (tenant,[row['primary_agent_id'],row['expert_agent_id']]))
            agents={str(agent['id']):agent for agent in c.fetchall()}
            current_primary=agents.get(str(row['primary_agent_id']));current_expert=agents.get(str(row['expert_agent_id']))
            valid=(current_primary and current_primary['role']=='primary' and current_primary['enabled'] is True
                   and current_primary['mode']=='manual' and not current_primary.get('scopes')
                   and current_expert and current_expert['role']=='expert' and current_expert['enabled'] is True
                   and current_expert['mode']=='manual' and not current_expert.get('scopes'))
            if valid:
                from tenant_ai_expert_policy import helper_ids,policy as expert_policy
                valid=str(row['expert_agent_id']) in helper_ids(expert_policy(current_primary.get('expert_policy') or {}))
            if not valid:
                c.execute("UPDATE saas_ai_instructor_jobs SET status='blocked',error_code='agent_configuration_changed',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,job_id))
                _event(c,tenant,job_id,'blocked',{'reason':'agent_configuration_changed'})
                conn.commit();return None
            secret=json.loads(_fernet().decrypt(bytes(connection['credentials'])).decode())
            if secret.get('tenant_id')!=tenant or (secret.get('account_ref') and secret.get('account_ref')!=str(connection['account_ref'])):
                raise TenantLinearError('Connection credential scope mismatch',403)
            row=dict(row);row['tenant_id']=tenant;row['id']=job_id
            row['permission_level']=permission_level
            row['connection']={k:connection[k] for k in ('id','origin','account_ref','demo_company_id','attestation_ref','restore_receipt_ref')}
            row['credentials']=secret
        conn.commit()
        return row


def record_event(tenant_id,job_id,event_type,detail=None):
    tenant=identifier(tenant_id);job_id=identifier(job_id)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            _event(c,tenant,job_id,event_type,detail or {})
        conn.commit()


def record_action(tenant_id,job_id,action,target=None):
    """Atomically account for one allowlisted browser action before execution."""
    from tenant_ai_instructor_contract import ACTIONS,TARGETS
    tenant=identifier(tenant_id);job_id=identifier(job_id)
    if action not in ACTIONS or (target is not None and target not in TARGETS): raise TenantLinearError('Action not allowed',403)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            c.execute('''UPDATE saas_ai_instructor_jobs SET actions_used=actions_used+1,updated_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='running' AND actions_used<max_actions
                AND deadline_at>now() RETURNING actions_used,max_actions''',(tenant,job_id))
            row=c.fetchone()
            if not row: raise TenantLinearError('Job action/time budget exhausted',409)
            _event(c,tenant,job_id,'action_started',{'action':action,'target':target or ''})
        conn.commit()
    return dict(row)


def renew_lease(tenant_id,job_id):
    tenant=identifier(tenant_id);job_id=identifier(job_id)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            c.execute("UPDATE saas_ai_instructor_jobs SET lease_until=now()+interval '3 minutes',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='running' AND deadline_at>now() RETURNING id",(tenant,job_id))
            active=bool(c.fetchone())
        conn.commit()
    return active


def is_running(tenant_id,job_id):
    tenant=identifier(tenant_id);job_id=identifier(job_id)
    with _connect() as conn:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM saas_ai_instructor_jobs WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='running' AND deadline_at>now()",(tenant,job_id))
            return bool(c.fetchone())


def consume_tokens(tenant_id,job_id,amount):
    tenant=identifier(tenant_id);job_id=identifier(job_id)
    if type(amount) is not int or amount<0: raise ValueError('token_usage')
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            c.execute('''UPDATE saas_ai_instructor_jobs SET tokens_used=tokens_used+%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='running' AND tokens_used+%s<=budget_tokens
              RETURNING tokens_used,budget_tokens''',(amount,tenant,job_id,amount))
            row=c.fetchone()
            if not row: raise TenantLinearError('Job token budget exhausted',409)
        conn.commit()
    return dict(row)


def estimate_provider_tokens(prompt,model):
    """Tokenize with the selected provider model; unknown encodings fail closed."""
    try:
        import tiktoken
        encoding=tiktoken.encoding_for_model(str(model))
        return len(encoding.encode(str(prompt)))+256
    except Exception:
        raise TenantLinearError('AI Instructor requires a tokenizer for the selected model',503) from None


def reserve_token_budget(tenant_id,job_id,prompt,max_output_tokens,*,model=None):
    """Reserve model-tokenized input plus output and request-envelope overhead."""
    tenant=identifier(tenant_id);job_id=identifier(job_id)
    if not isinstance(prompt,str) or type(max_output_tokens) is not int or not 1<=max_output_tokens<=12000:
        raise ValueError('token_reservation')
    amount=estimate_provider_tokens(prompt,model)+max_output_tokens
    if amount>12000:raise TenantLinearError('Prompt exceeds the 12,000 token job ceiling',429)
    reservation_id=uuid.uuid4()
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            c.execute('''UPDATE saas_ai_instructor_jobs SET reserved_tokens=reserved_tokens+%s,ai_calls_used=ai_calls_used+1,updated_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='running'
                AND tokens_used+reserved_tokens+%s<=budget_tokens AND ai_calls_used<ai_calls_max RETURNING id''',(amount,tenant,job_id,amount))
            if not c.fetchone():raise TenantLinearError('Job token budget exhausted',429)
            c.execute('''INSERT INTO saas_ai_instructor_token_reservations(tenant_id,job_id,id,reserved_tokens)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s)''',(tenant,job_id,reservation_id,amount))
        conn.commit()
    return {'id':str(reservation_id),'reserved_tokens':amount}


def settle_token_budget(tenant_id,job_id,reservation_id,actual_tokens=None,*,uncertain=False):
    tenant=identifier(tenant_id);job_id=identifier(job_id);reservation_id=identifier(reservation_id)
    if not uncertain and (type(actual_tokens) is not int or actual_tokens<0):raise ValueError('token_usage')
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            c.execute('''SELECT reserved_tokens,status FROM saas_ai_instructor_token_reservations
              WHERE tenant_id=%s::uuid AND job_id=%s::uuid AND id=%s::uuid FOR UPDATE''',(tenant,job_id,reservation_id))
            reservation=c.fetchone()
            if not reservation:raise TenantLinearError('Token reservation not found',404)
            if reservation['status']!='reserved':return {'status':reservation['status'],'used_tokens':reservation['used_tokens'] if 'used_tokens' in reservation else 0}
            reserved=int(reservation['reserved_tokens']);used=reserved if uncertain else actual_tokens
            state='unknown' if uncertain else 'settled'
            c.execute('''UPDATE saas_ai_instructor_jobs SET reserved_tokens=GREATEST(0,reserved_tokens-%s),
              tokens_used=tokens_used+%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid
              RETURNING tokens_used,budget_tokens''',(reserved,used,tenant,job_id))
            totals=c.fetchone()
            c.execute('''UPDATE saas_ai_instructor_token_reservations SET status=%s,used_tokens=%s,settled_at=now()
              WHERE tenant_id=%s::uuid AND job_id=%s::uuid AND id=%s::uuid''',(state,used,tenant,job_id,reservation_id))
            if int(totals['tokens_used'])>int(totals['budget_tokens']):
                c.execute("UPDATE saas_ai_instructor_jobs SET status='blocked',error_code='token_budget_overrun',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='running'",(tenant,job_id))
                _event(c,tenant,job_id,'blocked',{'reason':'token_budget_overrun'})
        conn.commit()
    return {'status':state,'used_tokens':used,'tokens_used':totals['tokens_used'],'budget_tokens':totals['budget_tokens']}


def finish_job(tenant_id,job_id,state,error_code=None):
    tenant=identifier(tenant_id);job_id=identifier(job_id)
    transitions={'completed','blocked','failed','cancelled'}
    if state not in transitions: raise ValueError('job_state')
    code=(str(error_code or '')[:80] or None)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            c.execute('''UPDATE saas_ai_instructor_jobs SET status=%s,error_code=%s,lease_until=NULL,updated_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='running' RETURNING id''',(state,code,tenant,job_id))
            if not c.fetchone():
                c.execute("SELECT status FROM saas_ai_instructor_jobs WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,job_id))
                current=c.fetchone()
                if state!='cancelled' or not current or current.get('status')!='cancelled':
                    raise TenantLinearError('Job state conflict',409)
            _event(c,tenant,job_id,state,{'reason':code or ''})
        conn.commit()


def add_artifact(tenant_id,job_id,kind,path,mime,receipt_ref):
    tenant=identifier(tenant_id);job_id=identifier(job_id);artifact_id=str(uuid.uuid4())
    path=Path(path).resolve();root=_artifact_root()
    try: relative=path.relative_to(root)
    except ValueError: raise TenantLinearError('Artifact path is outside private storage',403) from None
    if kind not in {'screenshot','recording','source_recording','subtitle','scenario'} or not path.is_file(): raise ValueError('artifact')
    size=path.stat().st_size
    if size<1 or size>250_000_000: raise ValueError('artifact_size')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            c.execute('''INSERT INTO saas_ai_instructor_artifacts(tenant_id,job_id,id,kind,relpath,mime,size_bytes,sha256,sanitization_receipt_ref)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s::uuid)''',
              (tenant,job_id,artifact_id,kind,relative.as_posix(),mime,size,digest,identifier(receipt_ref)))
        conn.commit()
    return {'id':artifact_id,'kind':kind,'mime':mime,'size_bytes':size,'sha256':digest}


def private_artifact(profile,job_id,artifact_id):
    job_id=identifier(job_id);artifact_id=identifier(artifact_id)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            tenant,_actor=_authorize(c,profile)
            c.execute('''SELECT relpath,mime,size_bytes,sha256 FROM saas_ai_instructor_artifacts
              WHERE tenant_id=%s::uuid AND job_id=%s::uuid AND id=%s::uuid''',(tenant,job_id,artifact_id))
            row=c.fetchone()
            if not row: raise TenantLinearError('Not found',404)
    root=_artifact_root()
    path=(root/row['relpath']).resolve()
    try: path.relative_to(root)
    except ValueError: raise TenantLinearError('Not found',404) from None
    if not path.is_file() or path.stat().st_size!=row['size_bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
        raise TenantLinearError('Artifact unavailable',410)
    return path,row['mime']


def attach_recording_to_draft(profile,job_id,lesson_id,artifact_id):
    """Link a private rendered MP4 into an existing draft only; never publish it."""
    job_id=identifier(job_id);lesson_id=identifier(lesson_id);artifact_id=identifier(artifact_id)
    with _connect() as conn:
        from tenant_ai_agents_schema import ensure_schema
        ensure_schema(conn)
        with conn.cursor() as c:
            tenant,actor=_authorize(c,profile)
            c.execute("SELECT kind,mime FROM saas_ai_instructor_artifacts WHERE tenant_id=%s::uuid AND job_id=%s::uuid AND id=%s::uuid FOR SHARE",(tenant,job_id,artifact_id))
            artifact=c.fetchone()
            if not artifact or artifact['kind']!='recording' or artifact['mime']!='video/mp4': raise TenantLinearError('MP4 artifact not found',404)
            c.execute("SELECT state,doc FROM saas_instruction_lessons WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE",(tenant,lesson_id))
            lesson=c.fetchone()
            if not lesson or lesson['state']!='draft': raise TenantLinearError('A draft instruction is required',409)
            doc=lesson['doc'];doc['video']={'artifact_id':artifact_id,'job_id':job_id,'mime':'video/mp4','review_status':'pending_review'}
            c.execute("UPDATE saas_instruction_lessons SET doc=%s::jsonb,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(_json(doc),tenant,lesson_id))
            c.execute("UPDATE saas_ai_instructor_jobs SET lesson_id=%s::uuid,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='running'",(lesson_id,tenant,job_id))
            _audit(c,tenant,actor,'ai_instructor_recording_attached','instruction',lesson_id,{'job_id':job_id,'artifact_id':artifact_id,'published':False})
        conn.commit()
    return {'lesson_id':lesson_id,'state':'draft','published':False}


def _parse_srt(value):
    value=str(value or '')
    if not value or len(value)>12000:raise ValueError('subtitle_size')
    blocks=[part.strip() for part in re.split(r'\r?\n\s*\r?\n',value) if part.strip()]
    if not 1<=len(blocks)<=20:raise ValueError('subtitle_count')
    def stamp(raw):
        match=re.fullmatch(r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})',raw)
        if not match:raise ValueError('subtitle_timestamp')
        hours,minutes,seconds,millis=map(int,match.groups())
        return ((hours*60+minutes)*60+seconds)*1000+millis
    cues=[];previous=0
    for index,block in enumerate(blocks,1):
        rows=block.splitlines()
        if len(rows)<3 or rows[0].strip()!=str(index):raise ValueError('subtitle_sequence')
        timing=re.fullmatch(r'([^ ]+)\s+-->\s+([^ ]+)',rows[1].strip())
        if not timing:raise ValueError('subtitle_timestamp')
        start,end=stamp(timing.group(1)),stamp(timing.group(2));text=' '.join(rows[2:]).strip()
        if start<previous or end<=start or end>600000 or not 1<=len(text)<=120 or re.search(r'[<>\x00-\x08\x0b\x0c\x0e-\x1f]',text):
            raise ValueError('subtitle_text_or_timing')
        cues.append({'start_ms':start,'end_ms':end,'text':text});previous=end
    return cues


def rerender_draft_video(profile,job_id,subtitle_text):
    """Rebuild a private video from the retained browser recording; keep lesson a draft."""
    job_id=identifier(job_id);cues=_parse_srt(subtitle_text)
    from tenant_ai_instructor_media import render_mp4,srt_text
    tenant=None;actor=None;source=None;subtitle_path=None;video_path=None
    root=_artifact_root()
    try:
        with _connect() as conn:
            schema(conn)
            with conn.cursor() as c:
                tenant,actor=_authorize(c,profile)
                c.execute("SELECT id,lesson_id,status FROM saas_ai_instructor_jobs WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE",(tenant,job_id))
                job=c.fetchone()
                if not job or job.get('status')!='completed' or not job.get('lesson_id'):
                    raise TenantLinearError('A completed instructor draft is required',409)
                c.execute("SELECT state,doc,version FROM saas_instruction_lessons WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE",(tenant,job['lesson_id']))
                lesson=c.fetchone()
                if not lesson or lesson.get('state')!='draft':raise TenantLinearError('Only an unpublished draft can be rebuilt',409)
                c.execute("SELECT id,relpath,size_bytes,sha256 FROM saas_ai_instructor_artifacts WHERE tenant_id=%s::uuid AND job_id=%s::uuid AND kind='source_recording' ORDER BY created_at DESC LIMIT 1",(tenant,job_id))
                row=c.fetchone()
                if not row:raise TenantLinearError('Original browser recording is unavailable',410)
                source=(root/row['relpath']).resolve()
                try:source.relative_to(root)
                except ValueError:raise TenantLinearError('Artifact unavailable',404) from None
                if not source.is_file() or source.stat().st_size!=row['size_bytes'] or hashlib.sha256(source.read_bytes()).hexdigest()!=row['sha256']:
                    raise TenantLinearError('Original browser recording is unavailable',410)
                work=root/tenant/job_id/('subtitle-edit-'+str(uuid.uuid4()));work.mkdir(parents=True,exist_ok=False)
                subtitle_path=work/'captions.srt';video_path=work/'lesson.mp4'
                subtitle_path.write_text(srt_text(cues),encoding='utf-8')
                rendered=render_mp4(source,subtitle_path,video_path)
                subtitle_id=uuid.uuid4();video_id=uuid.uuid4()
                new_paths=[(subtitle_id,'subtitle',subtitle_path,'application/x-subrip'),
                           (video_id,'recording',video_path,'video/mp4')]
                for artifact_id,kind,path,mime in new_paths:
                    rel=path.resolve().relative_to(root).as_posix();size=path.stat().st_size
                    digest=hashlib.sha256(path.read_bytes()).hexdigest()
                    c.execute('''INSERT INTO saas_ai_instructor_artifacts(tenant_id,job_id,id,kind,relpath,mime,size_bytes,sha256,sanitization_receipt_ref)
                      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s::uuid)''',
                      (tenant,job_id,artifact_id,kind,rel,mime,size,digest,uuid.uuid4()))
                doc=lesson['doc'] if isinstance(lesson['doc'],dict) else json.loads(lesson['doc'])
                video=doc.get('video') if isinstance(doc.get('video'),dict) else {}
                video.update({'artifact_id':str(video_id),'job_id':job_id,'mime':'video/mp4','review_status':'pending_review'})
                doc['video']=video;doc['review_status']='pending_review'
                c.execute("UPDATE saas_instruction_lessons SET doc=%s::jsonb,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",
                    (_json(doc),tenant,job['lesson_id']))
                _audit(c,tenant,actor,'ai_instructor_subtitles_rerendered','instruction',job['lesson_id'],{'job_id':job_id,'published':False,'video_artifact_id':str(video_id)})
                _event(c,tenant,job_id,'video_rendered',{'kind':'recording','artifact_id':str(video_id)})
                conn.commit()
        return {'lesson_id':str(job['lesson_id']),'state':'draft','published':False,
                'subtitle_artifact_id':str(subtitle_id),'video_artifact_id':str(video_id),
                'video_size_bytes':rendered['size_bytes']}
    except Exception:
        for path in (subtitle_path,video_path):
            try:
                if path and path.exists():path.unlink()
            except Exception:pass
        raise


def save_generated_lesson_draft(profile,job_id,scenario,screenshot_paths,recording_artifact_id):
    """Create a draft in the existing Instructions library from verified captures."""
    job_id=identifier(job_id);artifact_id=identifier(recording_artifact_id)
    if not isinstance(scenario,dict) or not isinstance(scenario.get('slides'),list): raise ValueError('scenario')
    slides=scenario['slides'];screens=list(screenshot_paths or [])
    if not slides or len(slides)>20 or len(screens)!=len(slides): raise ValueError('screenshot_evidence_required')
    from tenant_instruction_library import operation as library_operation
    lesson_id=str(uuid.uuid4());assets=[]
    for path in screens:
        raw=Path(path).read_bytes()
        if len(raw)>2_000_000: raise ValueError('screenshot_size')
        assets.append(library_operation(profile,'upload',{'image':base64.b64encode(raw).decode('ascii')}))
    subtitles=scenario.get('subtitles') or []
    lesson_slides=[];subtitle_slides=[]
    for index,(item,image) in enumerate(zip(slides,assets),1):
        title=str(item.get('title') or '')[:120];text=str(item.get('text') or '')[:1800]
        cue=subtitles[index-1] if index-1<len(subtitles) else {'text':title}
        lesson_slides.append({'id':str(uuid.uuid4()),'order':index,'title':title,'explanation':text,'note':'',
          'image':image,'areas':[],'playback':{'duration_ms':12000,'transition_ms':650,'focus':[],'return_to_overview':True}})
        subtitle_slides.append([{'at_ms':0,'text':str(cue.get('text') or title)[:120]}])
    language=scenario.get('language') if scenario.get('language') in {'ru','az','en'} else 'az'
    doc={'schema_version':1,'id':lesson_id,'title':str(scenario.get('title') or 'AI Instructor draft')[:180],
      'language':language,
      'operation':'inventory.warehouse_transfer','review_status':'pending_review',
      'verification':{'status':'not_verified','environment':'AI Instructor test environment','ui_version':'unreported'},
      'slides':lesson_slides,'subtitles':{'language':language,'slides':subtitle_slides}}
    lesson=library_operation(profile,'save',{'id':lesson_id,'expected_version':0,'doc':doc},lesson_id)
    attached=attach_recording_to_draft(profile,job_id,lesson_id,artifact_id)
    return {'lesson_id':lesson_id,'state':lesson.get('state','draft'),'video':True,
            'published':False,'review_status':'pending_review',**attached}
