"""Disabled review candidate for the owner's cross-executor voice-test cap.

No routes import this module. No provider call is implemented here. Do not
enable until the server-owned response boundary, permissions, provider bounds,
single shared DB and migration/cutover have been independently verified.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from tenant_linear_policy import TenantLinearError

# Deliberate code gate as well as disabled-by-default DB grant. There is no
# environment variable or browser parameter that can make this true.
ENFORCEMENT_VERIFIED = False
RECONCILIATION_VERIFIED = False
GRANT_KEY = 'voice-test-owner-5usd-2026'
CAP = 5_000_000
QUOTE = 'mini-2026-11-03-1resp-v1'
MODEL = 'gpt-realtime-2.1-mini'
HOLD = 2_112_000
EXPIRES = datetime(2026, 11, 4, tzinfo=timezone.utc)


def _ready():
    if ENFORCEMENT_VERIFIED is not True or RECONCILIATION_VERIFIED is not True:
        raise TenantLinearError('voice_test_enforcement_unverified',409)


def _ready_reconciliation():
    # This independent gate needs trusted billing/identity verification. A kill
    # switch for new dispatch must never block already verified reconciliation.
    if RECONCILIATION_VERIFIED is not True:
        raise TenantLinearError('voice_test_reconciliation_unverified',409)


def _scope(value):
    required={'request_id','tenant_id','session_id','actor_id','channel_id','conversation_id','response_number','executor'}
    if not isinstance(value,dict) or set(value)!=required:
        raise TenantLinearError('voice_test_scope_invalid',409)
    s=dict(value)
    try:
        for key in ('request_id','tenant_id','session_id','channel_id'):
            if not isinstance(s[key],str): raise ValueError()
            s[key]=str(uuid.UUID(s[key]))
    except (ValueError,AttributeError):
        raise TenantLinearError('voice_test_scope_invalid',409)
    if (type(s['actor_id']) is not int or not 1<=s['actor_id']<2**63 or
        type(s['response_number']) is not int or s['response_number'] not in (1,2) or
        not isinstance(s['executor'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',s['executor']) or
        not isinstance(s['conversation_id'],str) or not 1<=len(s['conversation_id'])<=200):
        raise TenantLinearError('voice_test_scope_invalid',409)
    return s


def _fingerprint(s):
    return hashlib.sha256(json.dumps({'schema':1,'scope':s},sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _session_fingerprint(s):
    data={key:s[key] for key in ('tenant_id','session_id','actor_id','channel_id','conversation_id','executor')}
    return hashlib.sha256(json.dumps({'schema':1,'scope':data},sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _lock(cur):
    cur.execute('SELECT cap_microusd,enabled,frozen FROM saas_voice_test_grant WHERE grant_key=%s FOR UPDATE',(GRANT_KEY,))
    grant=cur.fetchone()
    if not grant or grant['cap_microusd']!=CAP or grant['enabled'] is not True or grant['frozen'] is not False:
        raise TenantLinearError('voice_test_grant_disabled_or_frozen',409)


def reserve(cur, scope):
    """Reserve global + tenant budget atomically in caller's transaction.

    Caller must perform real Copilot auth/consent/role/route checks in the
    same transaction and COMMIT before issuing a response claim. Never supply
    this raw scope directly from a browser. The caller owns rollback on error.
    """
    _ready();s=_scope(scope);_lock(cur)
    if datetime.now(timezone.utc)>=EXPIRES: raise TenantLinearError('voice_test_quote_expired',409)
    fp=_fingerprint(s)
    session_fp=_session_fingerprint(s)
    cur.execute('''INSERT INTO saas_voice_test_session(session_id,tenant_id,actor_id,channel_id,conversation_id,executor,fingerprint)
      VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(session_id) DO NOTHING''',
      (s['session_id'],s['tenant_id'],s['actor_id'],s['channel_id'],s['conversation_id'],s['executor'],session_fp))
    cur.execute('SELECT fingerprint FROM saas_voice_test_session WHERE session_id=%s',(s['session_id'],))
    session=cur.fetchone()
    if not session or session['fingerprint']!=session_fp:raise TenantLinearError('voice_test_session_scope_conflict',409)
    cur.execute('SELECT * FROM saas_voice_test_hold WHERE request_id=%s',(s['request_id'],))
    previous=cur.fetchone()
    if previous:
        if previous['fingerprint']!=fp: raise TenantLinearError('voice_test_scope_conflict',409)
        if previous['quote_version']!=QUOTE or previous['model']!=MODEL or previous['reserved_microusd']!=HOLD:
            raise TenantLinearError('voice_test_quote_changed',409)
        if previous['state']=='released': raise TenantLinearError('voice_test_released',409)
        return previous
    cur.execute('SELECT request_id FROM saas_voice_test_hold WHERE tenant_id=%s AND session_id=%s AND response_number=%s',
                (s['tenant_id'],s['session_id'],s['response_number']))
    if cur.fetchone(): raise TenantLinearError('voice_test_response_exists',409)
    cur.execute("SELECT COALESCE(sum(GREATEST(reserved_microusd,COALESCE(actual_microusd,0))),0) AS used FROM saas_voice_test_hold WHERE state<>'released'")
    if int(cur.fetchone()['used'])+HOLD>CAP: raise TenantLinearError('voice_test_budget_exhausted',429)
    # Order: global grant row, then existing tenant advisory lock. All new
    # voice paths must respect this order; existing non-voice paths take only
    # the tenant lock. Never commit one reservation without the other.
    from tenant_ai_spend_budget import reserve as reserve_tenant
    key='voice-test:'+s['request_id']
    tenant_hold=reserve_tenant(cur,s['tenant_id'],key,'realtime_voice_test',MODEL,128000,32000,
                               track_uncapped=True,_bounded_prices=(11_000_000,22_000_000))
    cur.execute('''SELECT id,kind,model,reserved_microusd,state FROM saas_ai_spend_reservations
      WHERE tenant_id=%s AND id=%s''',(s['tenant_id'],tenant_hold))
    existing=cur.fetchone()
    if (not existing or existing['kind']!='realtime_voice_test' or existing['model']!=MODEL or
        existing['reserved_microusd']!=HOLD or existing['state']!='reserved'):
        raise TenantLinearError('voice_test_tenant_hold_conflict',409)
    cur.execute('''INSERT INTO saas_voice_test_hold(request_id,tenant_id,session_id,actor_id,channel_id,conversation_id,
      executor,response_number,fingerprint,quote_version,model,reserved_microusd,tenant_reservation_id,state)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reserved') RETURNING *''',
      (s['request_id'],s['tenant_id'],s['session_id'],s['actor_id'],s['channel_id'],s['conversation_id'],s['executor'],
       s['response_number'],fp,QUOTE,MODEL,HOLD,tenant_hold))
    return cur.fetchone()


def claim(cur,scope):
    """Claim exactly once, then COMMIT before any hypothetical provider send.

    Caller must repeat Copilot authorization and enforce upstream controls; a
    claim alone never authorizes network, microphone, tools or billing.
    """
    _ready();s=_scope(scope);_lock(cur)
    if datetime.now(timezone.utc)>=EXPIRES: raise TenantLinearError('voice_test_quote_expired',409)
    cur.execute('SELECT * FROM saas_voice_test_hold WHERE request_id=%s AND fingerprint=%s FOR UPDATE',
                (s['request_id'],_fingerprint(s)))
    row=cur.fetchone()
    if not row or row['state']!='reserved': return None
    if row['quote_version']!=QUOTE or row['model']!=MODEL or row['reserved_microusd']!=HOLD:
        raise TenantLinearError('voice_test_quote_changed',409)
    # Existing tenant cap may have changed after reservation. Keep lock order
    # consistent with reserve and validate both ledgers immediately pre-claim.
    from tenant_ai_spend_budget import limit_microusd
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+s['tenant_id'],))
    cur.execute('''SELECT kind,model,reserved_microusd,state FROM saas_ai_spend_reservations
      WHERE tenant_id=%s AND id=%s AND call_key=%s FOR UPDATE''',
      (s['tenant_id'],row['tenant_reservation_id'],'voice-test:'+s['request_id']))
    tenant_hold=cur.fetchone()
    if (not tenant_hold or tenant_hold['kind']!='realtime_voice_test' or tenant_hold['model']!=MODEL or
        tenant_hold['reserved_microusd']!=HOLD or tenant_hold['state']!='reserved'):
        raise TenantLinearError('voice_test_tenant_hold_conflict',409)
    tenant_cap=limit_microusd(s['tenant_id'])
    if tenant_cap:
        cur.execute('''SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_microusd
          ELSE COALESCE(actual_microusd,reserved_microusd) END),0) AS used FROM saas_ai_spend_reservations WHERE tenant_id=%s''',
          (s['tenant_id'],))
        if int(cur.fetchone()['used'])>tenant_cap:raise TenantLinearError('ai_spend_budget_exhausted',429)
    # Existing generic release/settle operate only on `reserved`. Moving this
    # tenant hold to `unknown` under the same lock before the global claim
    # prevents those paths from releasing or undercounting a dispatched call.
    # We intentionally retain the full tenant hold until a separate reviewed
    # settlement policy exists; do not record the hold as actual spend.
    cur.execute("UPDATE saas_ai_spend_reservations SET state='unknown' WHERE tenant_id=%s AND id=%s AND state='reserved' RETURNING id",
                (s['tenant_id'],row['tenant_reservation_id']))
    if not cur.fetchone():raise TenantLinearError('voice_test_tenant_hold_conflict',409)
    cur.execute("UPDATE saas_voice_test_hold SET state='claimed',updated_at=now() WHERE request_id=%s AND state='reserved' RETURNING *",(s['request_id'],))
    return cur.fetchone()


def mark_unknown(cur,scope):
    _ready_reconciliation();s=_scope(scope)
    cur.execute("UPDATE saas_voice_test_hold SET state='unknown',updated_at=now() WHERE request_id=%s AND fingerprint=%s AND state='claimed' RETURNING *",
                (s['request_id'],_fingerprint(s)))
    return cur.fetchone()


def _restore_tenant_hold(cur,scope,row):
    """Restore only this exactly identified hold; tenant advisory lock required.

    This repair cannot retract an operation admitted before repair commits and
    cannot prove old workers or direct provider paths participate in the lock.
    """
    key='voice-test:'+scope['request_id']
    expected_id=str(row['tenant_reservation_id'])
    cur.execute('''SELECT id,call_key,kind,model,state FROM saas_ai_spend_reservations
      WHERE tenant_id=%s AND (id=%s OR call_key=%s) ORDER BY id FOR UPDATE''',
      (scope['tenant_id'],expected_id,key))
    matches=cur.fetchall()
    amount=max(row['reserved_microusd'],row['actual_microusd'])
    if not matches:
        # Normally the FK prevents a missing hold. This path is only for a
        # damaged/incompletely migrated DB: reconstruct exact immutable scope.
        cur.execute('''INSERT INTO saas_ai_spend_reservations
          (tenant_id,id,call_key,kind,model,reserved_microusd,state)
          VALUES(%s,%s,%s,'realtime_voice_test',%s,%s,'unknown')
          ON CONFLICT DO NOTHING RETURNING id''',
          (scope['tenant_id'],expected_id,key,row['model'],amount))
        return 'recreated' if cur.fetchone() else 'conflict'
    if len(matches)!=1:return 'conflict'
    existing=matches[0]
    if (str(existing['id'])!=expected_id or existing['call_key']!=key or
        existing['kind']!='realtime_voice_test' or existing['model']!=row['model']):
        return 'conflict'
    repair='intact' if existing['state']=='unknown' else 'restored'
    cur.execute('''UPDATE saas_ai_spend_reservations
      SET state='unknown',reserved_microusd=GREATEST(reserved_microusd,COALESCE(actual_microusd,0),%s)
      WHERE tenant_id=%s AND id=%s AND call_key=%s AND kind='realtime_voice_test' AND model=%s RETURNING id''',
      (amount,scope['tenant_id'],expected_id,key,row['model']))
    return repair if cur.fetchone() else 'conflict'


def note_actual(cur,scope,actual_microusd):
    """Record trusted billing reconciliation; never refund either reserve.

    Provider usage authentication and aggregation are not implemented here.
    An overrun freezes the grant but cannot undo already dispatched requests.
    """
    _ready_reconciliation();s=_scope(scope)
    if type(actual_microusd) is not int or not 0<=actual_microusd<2**63:
        raise TenantLinearError('voice_test_usage_invalid',409)
    cur.execute('SELECT grant_key FROM saas_voice_test_grant WHERE grant_key=%s FOR UPDATE',(GRANT_KEY,))
    if not cur.fetchone(): raise TenantLinearError('voice_test_grant_missing',409)
    cur.execute('''UPDATE saas_voice_test_hold SET actual_microusd=GREATEST(COALESCE(actual_microusd,0),%s),
      state='quarantined',updated_at=now() WHERE request_id=%s AND fingerprint=%s
      AND state IN ('claimed','unknown','settled','quarantined') RETURNING *''',(actual_microusd,s['request_id'],_fingerprint(s)))
    row=cur.fetchone()
    if not row:return None
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+s['tenant_id'],))
    repair=_restore_tenant_hold(cur,s,row)
    previous=row['tenant_repair_state']
    # Preserve prior repair/conflict evidence on later usage corrections.
    # A conflict requires an explicit reviewed recovery, not an automatic clear.
    outcome='conflict' if 'conflict' in (repair,previous) else (previous if previous!='intact' else repair)
    if outcome!='intact' or row['actual_microusd']>row['reserved_microusd']:
        cur.execute('UPDATE saas_voice_test_grant SET frozen=true WHERE grant_key=%s',(GRANT_KEY,))
    cur.execute("UPDATE saas_voice_test_hold SET state=%s,tenant_repair_state=%s WHERE request_id=%s RETURNING *",
                ('quarantined' if outcome=='conflict' else 'settled',outcome,s['request_id']))
    row=cur.fetchone()
    return row


def cancel_before_claim(cur,scope):
    """Release both reservations atomically only while dispatch is impossible."""
    _ready_reconciliation();s=_scope(scope)
    cur.execute('SELECT grant_key FROM saas_voice_test_grant WHERE grant_key=%s FOR UPDATE',(GRANT_KEY,))
    if not cur.fetchone(): raise TenantLinearError('voice_test_grant_missing',409)
    cur.execute("UPDATE saas_voice_test_hold SET state='released',updated_at=now() WHERE request_id=%s AND fingerprint=%s AND state='reserved' RETURNING *",
                (s['request_id'],_fingerprint(s)))
    row=cur.fetchone()
    if not row:return None
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+s['tenant_id'],))
    cur.execute("UPDATE saas_ai_spend_reservations SET state='released',actual_microusd=0,completed_at=now() WHERE tenant_id=%s AND id=%s AND call_key=%s AND kind='realtime_voice_test' AND state='reserved' RETURNING id",
                (s['tenant_id'],row['tenant_reservation_id'],'voice-test:'+s['request_id']))
    if not cur.fetchone(): raise TenantLinearError('voice_test_tenant_hold_conflict',409)
    return row
