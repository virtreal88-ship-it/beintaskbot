"""Durable single-audio attempts and narrow cross-path read fences."""
import hashlib
import json
import uuid
from tenant_platform import _json
from tenant_linear_policy import TenantLinearError

ACTIVE={'reserved','sending','unknown'}


def exists(cur):
    cur.execute("SELECT to_regclass('public.saas_dialog_audio_attempts') AS relation")
    return bool((cur.fetchone() or {}).get('relation'))


def schema(conn):
    from tenant_chat_ai_schema import ensure_schema as chat_schema
    from tenant_ai_spend_budget import ensure_schema as budget_schema
    chat_schema(conn);budget_schema(conn)
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('dialog-audio-schema',))
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_dialog_audio_sources(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id),message_row_id UUID NOT NULL,
          channel_id UUID NOT NULL,external_id TEXT NOT NULL,generation BIGINT NOT NULL DEFAULT 0,
          active_attempt_id UUID,current_success_id UUID,
          PRIMARY KEY(tenant_id,message_row_id))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_dialog_audio_attempts(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id),id UUID NOT NULL,message_row_id UUID NOT NULL,selection_row_id UUID NOT NULL,selection_version TEXT NOT NULL,
          channel_id UUID NOT NULL,external_id TEXT NOT NULL,source_version TEXT NOT NULL,
          view_channel_id UUID NOT NULL,view_conversation_id TEXT NOT NULL,source_aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
          generation BIGINT NOT NULL,origin TEXT NOT NULL,actor_id BIGINT NOT NULL,
          idempotency_key UUID,input_hash TEXT NOT NULL DEFAULT '',cost_token_hash TEXT NOT NULL DEFAULT '',
          agent_id UUID,agent_binding TEXT NOT NULL DEFAULT '',config_version TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '',request_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
          quote_microusd BIGINT NOT NULL DEFAULT 0,call_key TEXT NOT NULL DEFAULT '',
          state TEXT NOT NULL CHECK(state IN ('reserved','sending','completed','failed_safe','unknown')),
          code TEXT NOT NULL DEFAULT '',dispatched_at TIMESTAMPTZ,
          original_sha256 TEXT,upload_sha256 TEXT,upload_mime TEXT,upload_extension TEXT,
          transcript TEXT NOT NULL DEFAULT '',usage JSONB NOT NULL DEFAULT '{}'::jsonb,
          published BOOLEAN NOT NULL DEFAULT FALSE,source_verified BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),completed_at TIMESTAMPTZ,
          PRIMARY KEY(tenant_id,id))''')
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS dialog_audio_request ON saas_dialog_audio_attempts(tenant_id,idempotency_key) WHERE idempotency_key IS NOT NULL")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS dialog_audio_generation ON saas_dialog_audio_attempts(tenant_id,message_row_id,generation) WHERE origin IN ('manual','reuse')")
        cur.execute('CREATE INDEX IF NOT EXISTS dialog_audio_source ON saas_dialog_audio_attempts(tenant_id,channel_id,external_id,state)')
        cur.execute('CREATE INDEX IF NOT EXISTS dialog_audio_selection ON saas_dialog_audio_attempts(tenant_id,selection_row_id,selection_version)')
        cur.execute('CREATE INDEX IF NOT EXISTS dialog_audio_upload ON saas_dialog_audio_attempts(tenant_id,upload_sha256,state)')
    conn.commit()


def lock_source(cur,tenant,channel,external):
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('voice-source:'+str(tenant)+':'+str(channel)+':'+str(external),))


def lock_bytes(cur,tenant,sha):
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('dialog-audio-bytes:'+str(tenant)+':'+str(sha),))


def blocked(cur,tenant,*,row_id=None,channel=None,external=None,hashes=(),exclude=None):
    """Only actual source/byte matches, never unrelated tenant-wide unknowns."""
    if not exists(cur):return None
    cur.execute('''SELECT state FROM saas_dialog_audio_attempts
      WHERE tenant_id=%s::uuid AND state IN ('reserved','sending','unknown')
        AND (%s::uuid IS NULL OR id<>%s::uuid)
        AND ((%s::uuid IS NOT NULL AND message_row_id=%s::uuid)
          OR (%s::text IS NOT NULL AND channel_id=%s::uuid AND external_id=%s)
          OR (%s::boolean AND source_aliases @> %s::jsonb)
          OR original_sha256=ANY(%s::text[]) OR upload_sha256=ANY(%s::text[]))
      ORDER BY (state='unknown') DESC LIMIT 1''',
      (tenant,exclude,exclude,row_id,row_id,external,channel,external,bool(channel and external),_json([{'channel_id':channel,'external_id':external}]),list(hashes),list(hashes)))
    row=cur.fetchone()
    return ('unknown' if row['state']=='unknown' else 'pending') if row else None


def current(cur,tenant,row_id,version,*,selection_version=None):
    if not exists(cur):return None
    cur.execute('''SELECT a.* FROM saas_dialog_audio_attempts a JOIN saas_dialog_audio_sources h
      ON h.tenant_id=a.tenant_id AND h.message_row_id=a.message_row_id AND h.current_success_id=a.id
      WHERE a.tenant_id=%s::uuid AND a.message_row_id=%s::uuid AND a.source_version=%s
        AND a.state='completed' AND a.published=TRUE AND a.source_verified=TRUE AND a.transcript<>''
        AND (%s::text IS NULL OR a.selection_version=%s)''',(tenant,row_id,version,selection_version,selection_version))
    row=cur.fetchone()
    return dict(row) if row else None


def current_ref(cur,tenant,ref):
    if not exists(cur):return None
    from tenant_media_evidence import source_hash
    cur.execute('''SELECT a.* FROM saas_dialog_audio_attempts a JOIN saas_dialog_audio_sources h
      ON h.tenant_id=a.tenant_id AND h.message_row_id=a.message_row_id AND h.current_success_id=a.id
      WHERE a.tenant_id=%s::uuid AND a.channel_id=%s::uuid AND a.external_id=%s AND a.source_version=%s
        AND a.state='completed' AND a.published=TRUE AND a.source_verified=TRUE AND a.transcript<>'' LIMIT 1''',
      (tenant,ref['channel_id'],ref['external_id'],source_hash(ref)))
    row=cur.fetchone()
    return dict(row) if row else None


def head(cur,tenant,row_id):
    if not exists(cur):return {'generation':0}
    cur.execute('SELECT * FROM saas_dialog_audio_sources WHERE tenant_id=%s::uuid AND message_row_id=%s::uuid',(tenant,row_id))
    return dict(cur.fetchone() or {'generation':0})


def import_success(cur,auth,item):
    identity=uuid.uuid5(uuid.NAMESPACE_URL,'dialog-audio-history:'+str(auth['tenant'])+':'+str(auth['row']['id'])+':'+item['origin']+':'+item['source_version']+':'+hashlib.sha256(item['text'].encode()).hexdigest())
    cur.execute('''INSERT INTO saas_dialog_audio_attempts
      (tenant_id,id,message_row_id,selection_row_id,selection_version,channel_id,external_id,source_version,view_channel_id,view_conversation_id,
       generation,origin,actor_id,model,state,transcript,published,source_verified,completed_at)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s::uuid,%s,0,%s,%s,%s,'completed',%s,TRUE,%s,now())
      ON CONFLICT DO NOTHING''',
      (auth['tenant'],str(identity),str(auth['row']['id']),str(auth['selection']['id']),auth['selection_version'],auth['source_channel'],auth['row']['external_id'],item['source_version'],
       auth['view_channel'],auth['view_conversation'],item['origin'],auth['actor'],item.get('model') or '',item['text'],item['source_current']))
    return str(identity)


def legacy_guards(cur,auth,*,hashes=(),fingerprints=()):
    """Durable source/byte evidence only; raw Copilot error summaries are ignored."""
    tenant=auth['tenant'];channel=auth['source_channel'];external=auth['row']['external_id'];row_id=str(auth['row']['id'])
    state=blocked(cur,tenant,row_id=row_id,channel=channel,external=external,hashes=hashes)
    if state:return state
    cur.execute("SELECT to_regclass('public.saas_episode_media_flights') AS relation")
    if (cur.fetchone() or {}).get('relation'):
        cur.execute('''SELECT state,result FROM saas_episode_media_flights WHERE tenant_id=%s::uuid
          AND ((channel_id=%s::uuid AND external_id=%s) OR result->>'sha256'=ANY(%s::text[]))
          AND (state IN ('running','unknown') OR result->>'code'='media_result_unknown') LIMIT 1''',(tenant,channel,external,list(hashes)))
        row=cur.fetchone()
        if row:return 'pending' if row['state']=='running' else 'unknown'
    cur.execute("SELECT to_regclass('public.saas_connect_voice_receipts') AS relation")
    if (cur.fetchone() or {}).get('relation'):
        cur.execute('SELECT state,error_code FROM saas_connect_voice_receipts WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND external_id=%s',(tenant,channel,external))
        row=cur.fetchone()
        if row and row['state']=='processing':return 'pending'
        safe={'voice_media_unavailable','voice_invalid_audio','voice_unsupported','voice_duration_limit','voice_size_limit','voice_decoder_unavailable','voice_decode_timeout'}
        if row and row['state']=='failed' and row.get('error_code') not in safe:return 'unknown'
    cur.execute("SELECT to_regclass('public.saas_knowledge_extractions') AS relation")
    if (cur.fetchone() or {}).get('relation'):
        cur.execute('''SELECT e.status FROM saas_knowledge_extractions e WHERE tenant_id=%s::uuid
          AND status='running' AND (source_refs @> %s::jsonb OR source_refs @> %s::jsonb) LIMIT 1''',
          (tenant,_json([{'id':row_id}]),_json([{'external_id':external,'channel_id':channel}])))
        if cur.fetchone():return 'pending'
        cur.execute("SELECT to_regclass('public.saas_ai_spend_reservations') AS relation")
        if (cur.fetchone() or {}).get('relation'):
            cur.execute('''SELECT s.state FROM saas_ai_spend_reservations s JOIN saas_knowledge_extractions e
              ON e.tenant_id=s.tenant_id AND s.call_key='extraction:'||e.id::text||':asr'
              WHERE s.tenant_id=%s::uuid AND s.state IN ('reserved','unknown')
                AND (e.source_refs @> %s::jsonb OR e.source_refs @> %s::jsonb) LIMIT 1''',
              (tenant,_json([{'id':row_id}]),_json([{'external_id':external,'channel_id':channel}])))
            row=cur.fetchone()
            if row:return 'unknown' if row['state']=='unknown' else 'pending'
    cur.execute("SELECT to_regclass('public.saas_knowledge_media_cache') AS relation")
    if (cur.fetchone() or {}).get('relation'):
        cur.execute('''SELECT state FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid AND state IN ('running','unknown')
          AND (fingerprint=ANY(%s::text[]) OR metadata->>'sha256'=ANY(%s::text[])
            OR (metadata->'owner_retry_source'->>'message_id'=%s)
            OR (metadata->'owner_retry_source'->>'channel_id'=%s AND metadata->'owner_retry_source'->>'external_id'=%s))
          ORDER BY (state='unknown') DESC LIMIT 1''',(tenant,list(fingerprints),list(hashes),row_id,channel,external))
        row=cur.fetchone()
        if row:return 'unknown' if row['state']=='unknown' else 'pending'
    return None


def selected_successes(cur,tenant,rows,*,view_channel=None,view_conversation=None):
    """One bounded batch lookup for already-authorized Dialogs rows."""
    if not rows or not exists(cur):return {}
    from tenant_media_evidence import source_hash,_body_hash,media_version
    versions={str(row['id']):source_hash({'external_id':row['external_id'],'body_hash':_body_hash(row.get('body')),
        'media_kind':row.get('message_type'),'media_version':media_version(row.get('raw'))}) for row in rows}
    cur.execute('''SELECT a.*,m.body AS physical_body,m.raw AS physical_raw,m.message_type AS physical_kind,
      m.channel AS physical_channel,m.external_id AS physical_external
      FROM saas_dialog_audio_attempts a JOIN saas_dialog_audio_sources h
        ON h.tenant_id=a.tenant_id AND h.message_row_id=a.message_row_id AND h.current_success_id=a.id
      JOIN saas_crm_messages m ON m.tenant_id=a.tenant_id AND m.id=a.message_row_id
      WHERE a.tenant_id=%s::uuid AND a.selection_row_id=ANY(%s::uuid[])
        AND a.state='completed' AND a.published=TRUE AND a.source_verified=TRUE AND a.transcript<>''
        AND (%s::uuid IS NULL OR a.view_channel_id=%s::uuid)
        AND (%s::text IS NULL OR a.view_conversation_id=%s)
      ORDER BY a.created_at DESC,a.id DESC''',
      (tenant,list(versions),view_channel,view_channel,view_conversation,view_conversation))
    result={}
    for item in cur.fetchall():
        selected=str(item['selection_row_id'])
        actual=source_hash({'external_id':item['physical_external'],'body_hash':_body_hash(item.get('physical_body')),
            'media_kind':item['physical_kind'],'media_version':media_version(item.get('physical_raw'))})
        if selected not in result and versions[selected]==item['selection_version'] and actual==item['source_version']:
            result[selected]=dict(item)
    return result


def known_success(cur,tenant,row_id,version,selection_version):
    """Paid ASR remains reusable under fresh authority even if publication was withheld."""
    if not exists(cur):return None
    cur.execute('''SELECT * FROM saas_dialog_audio_attempts WHERE tenant_id=%s::uuid AND message_row_id=%s::uuid
      AND source_version=%s AND selection_version=%s AND state='completed' AND source_verified=TRUE AND transcript<>''
      ORDER BY generation DESC,created_at DESC,id DESC LIMIT 1''',(tenant,row_id,version,selection_version))
    row=cur.fetchone()
    return dict(row) if row else None
