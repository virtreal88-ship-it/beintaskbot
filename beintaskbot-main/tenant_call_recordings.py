"""Owner-only lab import; current source grants protect every subsequent read.

No provider credentials, outbound messaging, automatic calls or AI processing.
The public-key binding is an explicit, expiring deployment configuration.
"""
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from tenant_call_recording_bundle import decode, timestamp
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route
from tenant_platform import _connect, _fernet, _json


def migrate(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_test_call_recordings (
      id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      channel_id UUID NOT NULL, conversation_id TEXT NOT NULL, lead_id BIGINT NOT NULL,
      actor_id BIGINT NOT NULL, call_digest TEXT NOT NULL, bundle_digest TEXT NOT NULL,
      duration_ms INTEGER NOT NULL CHECK(duration_ms BETWEEN 1 AND 900000),
      complete BOOLEAN NOT NULL, end_code TEXT NOT NULL, encrypted BYTEA,
      happened_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE(tenant_id,channel_id,call_digest))''')
    # Compatible widening, once per existing table. Keep a named CHECK; no data
    # changes and no admission of longer legacy imports (decoder stays at 120s).
    cur.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='saas_test_call_recordings'::regclass
        AND conname='saas_test_call_recordings_duration_ms_check'
        AND pg_get_constraintdef(oid) LIKE '%120000%') THEN
        ALTER TABLE saas_test_call_recordings DROP CONSTRAINT saas_test_call_recordings_duration_ms_check;
        ALTER TABLE saas_test_call_recordings ADD CONSTRAINT saas_test_call_recordings_duration_ms_check
          CHECK(duration_ms BETWEEN 1 AND 900000);
      END IF;
    END $$""")
    cur.execute('ALTER TABLE saas_test_call_recordings ADD COLUMN IF NOT EXISTS browser_call_id UUID')
    cur.execute("ALTER TABLE saas_test_call_recordings ADD COLUMN IF NOT EXISTS audio_type TEXT NOT NULL DEFAULT 'audio/wav'")
    cur.execute('CREATE INDEX IF NOT EXISTS saas_test_call_recordings_expiry ON saas_test_call_recordings(expires_at) WHERE encrypted IS NOT NULL')


def binding(profile):
    try:
        raw = os.environ.get('BEINTASKBOT_CALL_RECORDING_PILOT', '')
        if len(raw)>8192:
            return None
        b = json.loads(raw)
        if (not isinstance(b,dict) or str(b['tenant_id']) != str(profile['tenant_id'])
                or timestamp(b['expires_at']) <= datetime.now(timezone.utc)
                or len(bytes.fromhex(b['public_key'])) != 32 or not isinstance(b['routes'],dict)
                or not 1 <= len(b['routes']) <= 5):
            return None
        uuid.UUID(b['tenant_id']);uuid.UUID(b['channel_id']);int(b['actor_id'])
        return b
    except (ValueError,KeyError,TypeError):
        return None


def _authorize(cur, profile, route, *, write=False):
    tenant, actor, policy = access(cur,profile,'dialogs',action='update' if write else 'view')
    b = binding(profile)
    if not b or (write and (policy.profile.get('role')!='owner' or actor!=int(b['actor_id']))):
        raise TenantLinearError('Тестовые записи недоступны.',403)
    try:
        channel = str(uuid.UUID(str(route['channel_id'])))
        lead = int(route['lead_id'])
        conversation = str(route['conversation_id'])
    except (KeyError,TypeError,ValueError):
        raise TenantLinearError('Диалог недоступен.',404) from None
    if channel != b['channel_id'] or not any(
            str(v.get('conversation_id'))==conversation and str(v.get('lead_id'))==str(lead)
            for v in b['routes'].values() if isinstance(v,dict)):
        raise TenantLinearError('Диалог не входит в тест записи.',403)
    cur.execute('''SELECT active FROM saas_connect_channels WHERE tenant_id=%s::uuid
      AND connect_channel_id=%s::uuid FOR SHARE''',(tenant,channel))
    source=cur.fetchone()
    archived=b.get('source_mode')=='archived_owner_binding'
    if not source or (source['active'] is not True and not archived):
        raise TenantLinearError('Тестовый источник отключён.',403)
    selected = assert_route(cur,tenant,policy,'connect',conversation,channel,lead,action='update' if write else 'view')
    # Lifecycle/alias writers must not move this exact physical route after
    # authorization but before the file is inserted/read. A changed route is
    # rejected, never silently redirected to another conversation or lead.
    cur.execute('''SELECT lead_id FROM saas_connect_conversations WHERE tenant_id=%s::uuid
      AND connect_channel_id=%s::uuid AND conversation_id=%s AND lead_id=%s FOR SHARE''',
      (tenant,channel,conversation,lead))
    if not cur.fetchone():
        raise TenantLinearError('Адрес тестового диалога изменился.',409)
    if selected.get('conversation_kind')=='group' or conversation.endswith('@g.us'):
        raise TenantLinearError('Тест допускает только подтверждённый личный диалог.',403)
    return tenant,actor,b,selected


def capability(profile, route):
    with _connect() as conn, conn.cursor() as cur:
        _authorize(cur,profile,route,write=True)
    return {'import_allowed':True,'retention_hours':24,'max_seconds':120}


def offer_import(profile, route):
    """UI hint only. save() always rechecks live authority and the exact route."""
    b=binding(profile)
    return bool(b and profile.get('role')=='owner' and str(profile.get('telegram_id'))==str(b['actor_id'])
        and str(route.get('channel_id'))==b['channel_id'] and any(isinstance(v,dict)
        and str(v.get('conversation_id'))==str(route.get('conversation_id'))
        and str(v.get('lead_id'))==str(route.get('lead_id')) for v in b['routes'].values()))


def save(profile, route, bundle):
    # Authenticate before doing expensive crypto; authorize again in the same
    # transaction as insertion so a revocation cannot race between these steps.
    capability(profile,route)
    b = binding(profile)
    if not b:
        raise TenantLinearError('Тест записи завершён.',403)
    manifest,wav = decode(bundle,bytes.fromhex(b['public_key']))
    peer_route=b['routes'].get(manifest['peer_jid'])
    if (manifest['account_jid'] != b['account_jid'] or not isinstance(peer_route,dict)
            or any(str(peer_route.get(k))!=str(route.get(k)) for k in ('conversation_id','lead_id'))
            or b['channel_id']!=str(route.get('channel_id'))):
        raise TenantLinearError('Запись относится к другому тестовому диалогу.',409)
    if b.get('source_mode')=='archived_owner_binding':
        # One specific file explicitly selected by the owner. This does not
        # assert a live provider/source relationship or authorize future calls.
        pinned=b.get('archive_call') or {}
        if manifest['call_id']!=pinned.get('call_id') or manifest['sha256']!=pinned.get('sha256'):
            raise TenantLinearError('Для архивного диалога разрешена только согласованная запись.',403)
    with _connect() as conn, conn.cursor() as cur:
        tenant,actor,current,selected=_authorize(cur,profile,route,write=True)
        if current!=b:
            raise TenantLinearError('Настройка теста изменилась. Повторите проверку.',409)
        return persist(cur,tenant,actor,b,selected,manifest,wav,bundle)


def persist(cur,tenant,actor,b,selected,manifest,wav,bundle,*,encoded_audio=None):
    """Shared transaction body; callers must establish current exact authority."""
    digest=hashlib.sha256((manifest['account_jid']+'\0'+manifest['call_id']).encode()).hexdigest()
    bundle_digest=hashlib.sha256(bundle).hexdigest()
    encrypted=_fernet().encrypt(encoded_audio if encoded_audio is not None else wav)
    channel=str(selected['connect_channel_id']);conversation=selected['conversation_id'];lead=selected['lead_id']
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('test-call-recordings:'+tenant,))
    cur.execute('''SELECT id,bundle_digest,conversation_id,lead_id FROM saas_test_call_recordings
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND call_digest=%s FOR UPDATE''',(tenant,channel,digest))
    old=cur.fetchone()
    if old:
        if old['bundle_digest']!=bundle_digest or old['conversation_id']!=conversation or old['lead_id']!=lead:
            raise TenantLinearError('Конфликт повторного сохранения записи.',409)
        return {'id':str(old['id']),'duplicate':True}
    cur.execute("SELECT count(*) AS n FROM saas_test_call_recordings WHERE tenant_id=%s::uuid AND created_at>now()-interval '24 hours'",(tenant,))
    if cur.fetchone()['n']>=20:
        raise TenantLinearError('Достигнут лимит тестовых записей за 24 часа.',429)
    record_id=str(uuid.uuid4())
    cur.execute('''INSERT INTO saas_test_call_recordings
      (id,tenant_id,channel_id,conversation_id,lead_id,actor_id,call_digest,bundle_digest,
       duration_ms,complete,end_code,encrypted,happened_at,expires_at,audio_type)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz,%s::timestamptz,%s)''',
      (record_id,tenant,channel,conversation,lead,actor,digest,bundle_digest,manifest['duration_ms'],
       manifest['complete'],manifest['end_code'],encrypted,manifest['started_at'],manifest['expires_at'],
       'audio/ogg' if encoded_audio is not None else 'audio/wav'))
    meta={'recording_id':record_id,'duration_ms':manifest['duration_ms'],'complete':manifest['complete'],
          'call_account_number':manifest['account_jid'].split('@')[0],
          'expires_at':manifest['expires_at'],'conversationId':conversation,'test_recording':not b.get('managed',False),
          'archived_owner_binding':b.get('source_mode')=='archived_owner_binding'}
    # An internal timeline event, never an incoming customer message. This
    # path deliberately does not call provider send, AI, CRM or inbox jobs.
    cur.execute('''INSERT INTO saas_crm_messages
      (id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,raw)
      VALUES(%s::uuid,%s::uuid,%s,%s,'system',%s,'Beintaskbot','','call_recording','',%s::timestamptz,%s::jsonb)''',
      (uuid.uuid4(),tenant,lead,'internal:call-recording:'+record_id,'connect:'+channel,manifest['started_at'],_json(meta)))
    return {'id':record_id,'duplicate':False}


def load(profile, record_id, *, with_type=False):
    try:record_id=str(uuid.UUID(str(record_id)))
    except ValueError:raise TenantLinearError('Запись недоступна.',404) from None
    with _connect() as conn, conn.cursor() as cur:
        # Tenant filter precedes any row inspection. Fresh membership and route
        # authorization are required even when the ID is already known.
        cur.execute('''SELECT * FROM saas_test_call_recordings WHERE tenant_id=%s::uuid AND id=%s::uuid''',
                    (str(profile.get('tenant_id')),record_id))
        row=cur.fetchone()
        if not row:raise TenantLinearError('Запись недоступна.',404)
        if row.get('browser_call_id'):
            # Pairing readiness and a pilot feature deadline are not a read
            # entitlement. Recording access follows fresh dialog/source rights
            # for its stored physical route throughout its retention period.
            tenant,_,policy=access(cur,profile,'dialogs')
            selected=assert_route(cur,tenant,policy,'connect',row['conversation_id'],str(row['channel_id']),row['lead_id'])
            if (str(selected.get('connect_channel_id'))!=str(row['channel_id'])
                    or selected.get('conversation_id')!=row['conversation_id']
                    or selected.get('lead_id')!=row['lead_id']):
                raise TenantLinearError('Адрес записи изменился.',409)
        else:
            _authorize(cur,profile,row)
        if not row['encrypted'] or timestamp(row['expires_at'])<=datetime.now(timezone.utc):
            raise TenantLinearError('Срок хранения записи истёк.',410)
        audio=_fernet().decrypt(bytes(row['encrypted']))
    audio_type='audio/ogg' if row.get('audio_type')=='audio/ogg' else 'audio/wav'
    return (audio,audio_type) if with_type else audio


def purge():
    # Retain a small idempotency tombstone and internal timeline metadata; audio
    # is removed. Downloads reject expired bytes even if this job is delayed.
    with _connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE saas_test_call_recordings SET encrypted=NULL WHERE encrypted IS NOT NULL AND expires_at<=now()')


async def deliver(logger):
    import asyncio
    await asyncio.to_thread(purge)
