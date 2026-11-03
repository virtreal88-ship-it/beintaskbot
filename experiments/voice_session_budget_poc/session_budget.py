"""Disabled local session-wide voice reservation candidate, NOT a gateway.

Every function receives an existing transaction/cursor. The trusted caller must
perform/coordinate real authorization, source/lease checks and commit before
any provider I/O; none is implemented here. Never use with production DB/API.
"""
from datetime import datetime,timezone
import hashlib
import json
import re
import uuid

from tenant_linear_policy import TenantLinearError

ADMISSION_VERIFIED=False
RECONCILIATION_VERIFIED=False
CONFIG_ATTESTATION_VERIFIED=False
PROVIDER_RECEIPTS_VERIFIED=False
GRANT_KEY='voice-test-owner-5usd-2026'
CAP=5_000_000
MODEL='gpt-realtime-2.1-mini'
QUOTE='mini-2026-11-03-session-v1'
QUOTE_EXPIRES=datetime(2026,11,4,tzinfo=timezone.utc)
PER_RESPONSE=2_112_000
MAX_PCM_BYTES=5_760_000
MAX_FRAMES=1200
MAX_FRAME=9600


def _admission():
    if ADMISSION_VERIFIED is not True or RECONCILIATION_VERIFIED is not True or CONFIG_ATTESTATION_VERIFIED is not True:
        raise TenantLinearError('voice_session_enforcement_unverified',409)


def _reconcile():
    if RECONCILIATION_VERIFIED is not True:
        raise TenantLinearError('voice_session_reconciliation_unverified',409)


def _uuid(value):
    if not isinstance(value,str):raise ValueError()
    return str(uuid.UUID(value))


def _scope(value):
    keys={'session_id','tenant_id','actor_id','channel_id','conversation_id','executor','source_fingerprint','response_limit','asr_mode'}
    if not isinstance(value,dict) or set(value)!=keys:raise TenantLinearError('voice_session_scope_invalid',409)
    s=dict(value)
    try:
        for key in ('session_id','tenant_id','channel_id'):s[key]=_uuid(s[key])
    except (ValueError,AttributeError):raise TenantLinearError('voice_session_scope_invalid',409) from None
    if (type(s['actor_id']) is not int or not 1<=s['actor_id']<2**63 or
        type(s['response_limit']) is not int or s['response_limit'] not in (1,2) or
        not isinstance(s['executor'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',s['executor']) or
        not isinstance(s['source_fingerprint'],str) or not re.fullmatch(r'[a-f0-9]{64}',s['source_fingerprint']) or
        not isinstance(s['conversation_id'],str) or not 1<=len(s['conversation_id'])<=200):
        raise TenantLinearError('voice_session_scope_invalid',409)
    # Input ASR is separately billed and exact rounding/minimums/cap have not
    # been verified. Do not convert the published estimate into a bound.
    if s['asr_mode']!='off':raise TenantLinearError('voice_session_asr_bound_unverified',409)
    return s


def _fingerprint(s):
    return hashlib.sha256(json.dumps({'scope':s,'schema':1},sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _quote(s):return PER_RESPONSE*s['response_limit']


def _quote_fresh():
    if datetime.now(timezone.utc)>=QUOTE_EXPIRES:raise TenantLinearError('voice_session_quote_expired',409)


def _global(cur,*,require_enabled=True):
    cur.execute('SELECT cap_microusd,enabled,frozen FROM saas_voice_test_grant WHERE grant_key=%s FOR UPDATE',(GRANT_KEY,))
    row=cur.fetchone()
    if not row or row['cap_microusd']!=CAP or (require_enabled and (row['enabled'] is not True or row['frozen'] is not False)):
        raise TenantLinearError('voice_session_grant_disabled_or_frozen',409)


def _session(cur,s):
    cur.execute('SELECT * FROM saas_voice_session_budget WHERE session_id=%s FOR UPDATE',(s['session_id'],))
    row=cur.fetchone()
    if not row or row['fingerprint']!=_fingerprint(s):raise TenantLinearError('voice_session_scope_conflict',409)
    return row


def _tenant(cur,s,row,expected_state):
    from tenant_ai_spend_budget import limit_microusd
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+s['tenant_id'],))
    cur.execute('''SELECT id,kind,model,state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations
      WHERE tenant_id=%s AND id=%s AND call_key=%s FOR UPDATE''',
      (s['tenant_id'],row['tenant_reservation_id'],'voice-session:'+s['session_id']))
    hold=cur.fetchone()
    if (not hold or hold['kind']!='realtime_voice_session_test' or hold['model']!=MODEL or
        hold['state']!=expected_state or hold['reserved_microusd']!=row['reserved_microusd'] or hold['actual_microusd'] is not None):
        # Do not raise after the writes: the trusted caller must COMMIT this
        # voice-only freeze/evidence; unrelated tenant AI is not fenced.
        cur.execute('UPDATE saas_voice_test_grant SET frozen=true WHERE grant_key=%s',(GRANT_KEY,))
        target='uncertain' if row['state'] in ('opening','ready','uncertain') else row['state']
        cur.execute('UPDATE saas_voice_session_budget SET accounting_conflict=true,state=%s,updated_at=now() WHERE session_id=%s',
                    (target,s['session_id']))
        return False
    cap=limit_microusd(s['tenant_id'])
    if cap:
        cur.execute('''SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_microusd
          ELSE COALESCE(actual_microusd,reserved_microusd) END),0) AS used FROM saas_ai_spend_reservations WHERE tenant_id=%s''',(s['tenant_id'],))
        if int(cur.fetchone()['used'])>cap:raise TenantLinearError('ai_spend_budget_exhausted',429)
    return True


def reserve_session(cur,scope):
    """Reserve *all* planned Response costs before any hypothetical network."""
    _admission();s=_scope(scope);_global(cur)
    _quote_fresh()
    cur.execute('SELECT 1 AS present FROM saas_voice_test_hold LIMIT 1')
    if cur.fetchone():raise TenantLinearError('voice_session_legacy_conflict',409)
    cur.execute('SELECT * FROM saas_voice_session_budget WHERE session_id=%s FOR UPDATE',(s['session_id'],))
    previous=cur.fetchone()
    if previous:
        if previous['fingerprint']!=_fingerprint(s):raise TenantLinearError('voice_session_scope_conflict',409)
        if previous['quote_version']!=QUOTE or previous['model']!=MODEL or previous['reserved_microusd']!=_quote(s):
            raise TenantLinearError('voice_session_quote_changed',409)
        if previous['state']=='released':raise TenantLinearError('voice_session_released',409)
        return previous
    cur.execute('''SELECT session_id FROM saas_voice_session_budget WHERE tenant_id=%s AND actor_id=%s AND channel_id=%s
      AND conversation_id=%s AND state IN ('reserved','opening','ready','uncertain')''',
      (s['tenant_id'],s['actor_id'],s['channel_id'],s['conversation_id']))
    if cur.fetchone():raise TenantLinearError('voice_session_already_active',409)
    cur.execute("SELECT COALESCE(sum(GREATEST(reserved_microusd,COALESCE(actual_microusd,0))),0) AS used FROM saas_voice_session_budget WHERE state<>'released'")
    if int(cur.fetchone()['used'])+_quote(s)>CAP:raise TenantLinearError('voice_session_budget_exhausted',429)
    from tenant_ai_spend_budget import reserve
    hold_id=reserve(cur,s['tenant_id'],'voice-session:'+s['session_id'],'realtime_voice_session_test',MODEL,
                    128000*s['response_limit'],32000*s['response_limit'],track_uncapped=True,
                    _bounded_prices=(11_000_000,22_000_000))
    cur.execute('''SELECT id,kind,model,state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations
      WHERE tenant_id=%s AND id=%s''',(s['tenant_id'],hold_id))
    hold=cur.fetchone()
    if (not hold or hold['kind']!='realtime_voice_session_test' or hold['model']!=MODEL or hold['state']!='reserved' or
        hold['reserved_microusd']!=_quote(s) or hold['actual_microusd'] is not None):
        raise TenantLinearError('voice_session_tenant_hold_conflict',409)
    cur.execute('''INSERT INTO saas_voice_session_budget(session_id,tenant_id,actor_id,channel_id,conversation_id,executor,
      source_fingerprint,response_limit,asr_mode,model,quote_version,fingerprint,reserved_microusd,tenant_reservation_id,state)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reserved') RETURNING *''',
      (s['session_id'],s['tenant_id'],s['actor_id'],s['channel_id'],s['conversation_id'],s['executor'],s['source_fingerprint'],
       s['response_limit'],s['asr_mode'],MODEL,QUOTE,_fingerprint(s),_quote(s),hold_id))
    return cur.fetchone()


def issue_open(cur,scope):
    """Consume opening once, commit, then and only then attempt provider open."""
    _admission();s=_scope(scope);_global(cur)
    _quote_fresh()
    row=_session(cur,s)
    if row['state']!='reserved' or row['accounting_conflict']:return None
    if row['quote_version']!=QUOTE or row['model']!=MODEL or row['reserved_microusd']!=_quote(s):
        raise TenantLinearError('voice_session_quote_changed',409)
    if not _tenant(cur,s,row,'reserved'):return None
    cur.execute("UPDATE saas_ai_spend_reservations SET state='unknown' WHERE tenant_id=%s AND id=%s AND state='reserved' RETURNING id",
                (s['tenant_id'],row['tenant_reservation_id']))
    if not cur.fetchone():raise TenantLinearError('voice_session_tenant_hold_conflict',409)
    cur.execute("UPDATE saas_voice_session_budget SET state='opening',updated_at=now() WHERE session_id=%s AND state='reserved' RETURNING *",(s['session_id'],))
    return cur.fetchone()


def _config_ok(event,provider_session_id):
    """Check only known cost-sensitive fields; caller verifies event provenance."""
    try:
        if not isinstance(provider_session_id,str) or not re.fullmatch(r'sess_[a-zA-Z0-9_-]{1,100}',provider_session_id):return False
        if not isinstance(event,dict) or event.get('type')!='session.updated':return False
        cfg=event['session'];audio=cfg['audio'];incoming=audio['input'];outgoing=audio['output'];vad=incoming['turn_detection']
        return (cfg['id']==provider_session_id and cfg['type']=='realtime' and cfg['model']==MODEL and
          cfg.get('input_audio_transcription') is None and cfg.get('prompt') is None and cfg.get('tracing') is None and
          cfg['output_modalities']==['audio'] and cfg['tools']==[] and cfg['tool_choice']=='none' and cfg['max_output_tokens']==4096 and
          incoming['format']=={'type':'audio/pcm','rate':24000} and incoming['transcription'] is None and
          incoming.get('noise_reduction') is None and set(incoming)<={'format','transcription','turn_detection','noise_reduction'} and
          vad['type']=='server_vad' and vad['create_response'] is False and vad['interrupt_response'] is False and
          vad['idle_timeout_ms'] is None and set(vad)<={'type','create_response','interrupt_response','idle_timeout_ms','threshold','prefix_padding_ms','silence_duration_ms'} and
          outgoing['format']=={'type':'audio/pcm','rate':24000} and set(audio)<={'input','output'} and
          set(outgoing)<={'format','voice','speed'})
    except (KeyError,TypeError):return False


def accept_config(cur,scope,event,provider_session_id):
    """Only a trusted provider receiver may call this; failed ack stays uncertain."""
    _admission();s=_scope(scope);_global(cur);row=_session(cur,s)
    if row['state']!='opening':return None
    if datetime.now(timezone.utc)>=QUOTE_EXPIRES or not _config_ok(event,provider_session_id):
        cur.execute("UPDATE saas_voice_session_budget SET state='uncertain',updated_at=now() WHERE session_id=%s RETURNING *",(s['session_id'],))
        return cur.fetchone()  # caller MUST commit, then stop forwarding
    if not _tenant(cur,s,row,'unknown'):
        cur.execute('SELECT * FROM saas_voice_session_budget WHERE session_id=%s',(s['session_id'],))
        return cur.fetchone()
    payload={'provider_session_id':provider_session_id,'model':MODEL,'quote':QUOTE,'asr':'off','response_limit':s['response_limit']}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    cur.execute("""UPDATE saas_voice_session_budget SET state='ready',provider_session_id=%s,config_fingerprint=%s,updated_at=now()
      WHERE session_id=%s AND state='opening' RETURNING *""",(provider_session_id,digest,s['session_id']))
    return cur.fetchone()


def issue_audio(cur,scope,chunk_id,data):
    """Meter immutable PCM bytes before a one-use send; never stores audio."""
    _admission();s=_scope(scope)
    try:chunk_id=_uuid(chunk_id)
    except (ValueError,AttributeError):raise TenantLinearError('voice_audio_chunk_invalid',409) from None
    if type(data) is not bytes or not 2<=len(data)<=MAX_FRAME or len(data)%2:
        raise TenantLinearError('voice_audio_chunk_invalid',409)
    _global(cur);_quote_fresh();row=_session(cur,s)
    if row['state']!='ready' or not row['provider_session_id'] or not row['config_fingerprint']:return None
    if not _tenant(cur,s,row,'unknown'):return None
    cur.execute('''INSERT INTO saas_voice_session_audio(chunk_id,session_id,byte_count) VALUES(%s,%s,%s)
      ON CONFLICT(chunk_id) DO NOTHING RETURNING *''',(chunk_id,s['session_id'],len(data)))
    receipt=cur.fetchone()
    if not receipt:return None
    cur.execute('''UPDATE saas_voice_session_budget SET audio_bytes=audio_bytes+%s,audio_frames=audio_frames+1,updated_at=now()
      WHERE session_id=%s AND state='ready' AND audio_bytes+%s<=%s AND audio_frames+1<=%s RETURNING session_id''',
      (len(data),s['session_id'],len(data),MAX_PCM_BYTES,MAX_FRAMES))
    if not cur.fetchone():raise TenantLinearError('voice_audio_session_limit',409)
    return receipt  # commit before sending; a lost send must never be retried


def claim_response(cur,scope,response_id,ordinal):
    _admission();s=_scope(scope)
    try:response_id=_uuid(response_id)
    except (ValueError,AttributeError):raise TenantLinearError('voice_response_invalid',409) from None
    if type(ordinal) is not int or not 1<=ordinal<=s['response_limit']:raise TenantLinearError('voice_response_invalid',409)
    _global(cur);_quote_fresh();row=_session(cur,s)
    if row['state']!='ready' or not row['config_fingerprint']:return None
    if not _tenant(cur,s,row,'unknown'):return None
    cur.execute('SELECT ordinal,state FROM saas_voice_session_response WHERE session_id=%s ORDER BY ordinal',(s['session_id'],))
    previous=cur.fetchall()
    if any(r['state']!='completed' for r in previous) or ordinal!=len(previous)+1:return None
    cur.execute('''INSERT INTO saas_voice_session_response(response_id,session_id,ordinal,scope_fingerprint,state)
      VALUES(%s,%s,%s,%s,'claimed') ON CONFLICT DO NOTHING RETURNING *''',
      (response_id,s['session_id'],ordinal,_fingerprint(s)))
    return cur.fetchone()


def issue_response(cur,scope,response_id):
    _admission();s=_scope(scope)
    try:response_id=_uuid(response_id)
    except (ValueError,AttributeError):raise TenantLinearError('voice_response_invalid',409) from None
    _global(cur);_quote_fresh();row=_session(cur,s)
    if row['state']!='ready':return None
    if not _tenant(cur,s,row,'unknown'):return None
    cur.execute("""UPDATE saas_voice_session_response SET state='dispatching',updated_at=now()
      WHERE response_id=%s AND session_id=%s AND scope_fingerprint=%s AND state='claimed' RETURNING *""",
      (response_id,s['session_id'],_fingerprint(s)))
    return cur.fetchone()  # commit before Response send; never automatically retry


def record_response_complete(cur,scope,response_id,provider_response_id):
    """Stub for a trusted, authenticated provider receipt; not enabled today."""
    _reconcile()
    if PROVIDER_RECEIPTS_VERIFIED is not True:raise TenantLinearError('voice_response_receipt_unverified',409)
    s=_scope(scope)
    try:response_id=_uuid(response_id)
    except (ValueError,AttributeError):raise TenantLinearError('voice_response_invalid',409) from None
    if not isinstance(provider_response_id,str) or not re.fullmatch(r'resp_[A-Za-z0-9_-]{1,100}',provider_response_id):
        raise TenantLinearError('voice_response_receipt_invalid',409)
    _global(cur,require_enabled=False);row=_session(cur,s)
    if row['state']!='ready':return None
    cur.execute("""UPDATE saas_voice_session_response SET state='completed',provider_response_id=%s,updated_at=now()
      WHERE response_id=%s AND session_id=%s AND scope_fingerprint=%s AND state='dispatching' RETURNING *""",
      (provider_response_id,response_id,s['session_id'],_fingerprint(s)))
    return cur.fetchone()


def mark_uncertain(cur,scope):
    """Hold the whole session permanently on unknown open/response outcome."""
    _reconcile();s=_scope(scope);_global(cur,require_enabled=False);row=_session(cur,s)
    if row['state'] not in ('opening','ready','uncertain'):return None
    cur.execute("UPDATE saas_voice_session_response SET state='uncertain',updated_at=now() WHERE session_id=%s AND state IN ('claimed','dispatching')",(s['session_id'],))
    cur.execute("UPDATE saas_voice_session_budget SET state='uncertain',updated_at=now() WHERE session_id=%s RETURNING *",(s['session_id'],))
    return cur.fetchone()


def record_session_actual(cur,scope,actual_microusd):
    """Record trusted, aggregate usage; never refund the original session hold.

    No provider receipt verifier is implemented: the independent gate is
    false. On mismatched tenant ledger preserve global evidence and quarantine
    voice rather than claiming the company accounting was repaired.
    """
    _reconcile()
    if PROVIDER_RECEIPTS_VERIFIED is not True:raise TenantLinearError('voice_session_receipt_unverified',409)
    s=_scope(scope)
    if type(actual_microusd) is not int or not 0<=actual_microusd<2**63:raise TenantLinearError('voice_session_usage_invalid',409)
    _global(cur,require_enabled=False);row=_session(cur,s)
    if row['state'] not in ('opening','ready','uncertain','closed'):return None
    actual=max(actual_microusd,row['actual_microusd'] or 0)
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+s['tenant_id'],))
    cur.execute('''UPDATE saas_ai_spend_reservations SET reserved_microusd=GREATEST(reserved_microusd,%s)
      WHERE tenant_id=%s AND id=%s AND call_key=%s AND kind='realtime_voice_session_test' AND model=%s
      AND reserved_microusd>=%s AND state='unknown' AND actual_microusd IS NULL RETURNING id''',
      (actual,s['tenant_id'],row['tenant_reservation_id'],'voice-session:'+s['session_id'],row['model'],row['reserved_microusd']))
    conflict=not bool(cur.fetchone())
    anomaly=conflict or actual>row['reserved_microusd'] or row['accounting_conflict']
    if anomaly:cur.execute('UPDATE saas_voice_test_grant SET frozen=true WHERE grant_key=%s',(GRANT_KEY,))
    state='uncertain' if anomaly and row['state'] in ('opening','ready','uncertain') else row['state']
    cur.execute('''UPDATE saas_voice_session_budget SET actual_microusd=%s,accounting_conflict=%s,state=%s,updated_at=now()
      WHERE session_id=%s RETURNING *''',(actual,conflict or row['accounting_conflict'],state,s['session_id']))
    return cur.fetchone()  # caller must commit evidence/freeze even on conflict


def cancel_before_open(cur,scope):
    _reconcile();s=_scope(scope);_global(cur,require_enabled=False);row=_session(cur,s)
    if row['state']!='reserved' or row['accounting_conflict']:return None
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+s['tenant_id'],))
    cur.execute("""UPDATE saas_ai_spend_reservations SET state='released',actual_microusd=0,completed_at=now()
      WHERE tenant_id=%s AND id=%s AND call_key=%s AND kind='realtime_voice_session_test' AND model=%s
      AND reserved_microusd=%s AND actual_microusd IS NULL AND state='reserved' RETURNING id""",
      (s['tenant_id'],row['tenant_reservation_id'],'voice-session:'+s['session_id'],row['model'],row['reserved_microusd']))
    if not cur.fetchone():
        # As with dispatch and reconciliation, preserve evidence and commit the
        # voice-only freeze; never repair or release the unrelated tenant row.
        cur.execute('UPDATE saas_voice_test_grant SET frozen=true WHERE grant_key=%s',(GRANT_KEY,))
        cur.execute('UPDATE saas_voice_session_budget SET accounting_conflict=true,updated_at=now() WHERE session_id=%s',(s['session_id'],))
        return None
    cur.execute("UPDATE saas_voice_session_budget SET state='released',updated_at=now() WHERE session_id=%s AND state='reserved' RETURNING *",(s['session_id'],))
    return cur.fetchone()
