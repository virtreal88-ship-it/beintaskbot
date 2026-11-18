"""Experimental durable handoff; no HTTP route, CRM projection or sends yet.

The existing managed gateway credential and source binding remain authoritative.
Capture generations must be provisioned explicitly by a later rollout step. This
module never creates/activates a source, starts a device, or authorizes an owner.
It is intentionally unreachable from production startup until cutover fencing
and projection of existing provider IDs are implemented and verified.
"""
import hashlib
import hmac
import json
import os
import re
import uuid

from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect, _fernet

MAX_DELIVERY = (1 << 20) + 16384
HEX = re.compile(r'^[0-9a-f]{64}$')
GENERATION = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


def migrate(cur):
    # The first table is a capture cursor, NOT a provider ownership lease. It
    # must never be used as permission to connect/send or to activate a source.
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_whatsapp_capture_generations (
      tenant_id UUID NOT NULL, channel_id UUID NOT NULL, generation TEXT NOT NULL,
      gateway_id UUID NOT NULL, account_jid TEXT NOT NULL, enabled BOOLEAN NOT NULL DEFAULT FALSE,
      stored_sequence BIGINT NOT NULL DEFAULT 0 CHECK(stored_sequence>=0),
      PRIMARY KEY(tenant_id,channel_id,generation),
      FOREIGN KEY(tenant_id,channel_id) REFERENCES saas_connect_channels(tenant_id,connect_channel_id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_whatsapp_native_inbox (
      tenant_id UUID NOT NULL, channel_id UUID NOT NULL, generation TEXT NOT NULL,
      event_id TEXT NOT NULL, sequence BIGINT NOT NULL CHECK(sequence>0),
      after_sequence BIGINT NOT NULL CHECK(after_sequence>=0 AND after_sequence<sequence),
      payload_sha256 TEXT NOT NULL, encrypted_payload BYTEA NOT NULL,
      stored_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,channel_id,generation,event_id),
      UNIQUE(tenant_id,channel_id,generation,sequence),
      FOREIGN KEY(tenant_id,channel_id,generation)
        REFERENCES saas_whatsapp_capture_generations(tenant_id,channel_id,generation))''')


def _error(code='native_delivery_invalid', status=400):
    return TenantLinearError(code, status)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError()
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError()


def validate(raw):
    try:
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DELIVERY: raise ValueError()
        data = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_reject_constant)
        if not isinstance(data, dict) or set(data) != {'version','scope','after','event','proof'}: raise ValueError()
        if type(data['version']) is not int or data['version'] != 1: raise ValueError()
        scope, event = data['scope'], data['event']
        if not isinstance(scope, dict) or set(scope) != {'tenant','source','account','generation'}: raise ValueError()
        for key in ('tenant','source'):
            if not isinstance(scope[key], str) or str(uuid.UUID(scope[key])) != scope[key]: raise ValueError()
        if not isinstance(scope['account'], str) or not re.fullmatch(r'[1-9][0-9]{5,21}@s\.whatsapp\.net',scope['account']): raise ValueError()
        if not isinstance(scope['generation'], str) or not GENERATION.fullmatch(scope['generation']): raise ValueError()
        if not isinstance(event, dict) or set(event) != {'sequence','id','payload'}: raise ValueError()
        if type(data['after']) is not int or type(event['sequence']) is not int or not 0 <= data['after'] < event['sequence'] < 2**63: raise ValueError()
        if not isinstance(event['id'],str) or not HEX.fullmatch(event['id']): raise ValueError()
        if not isinstance(data['proof'],str) or not HEX.fullmatch(data['proof']): raise ValueError()
        if not isinstance(event['payload'],dict): raise ValueError()
        # Keep protocol keys/media references encrypted and out of ordinary CRM
        # JSON. This is an inbox, not a permissive message/URL projection.
        plain = json.dumps(event['payload'],ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()
        if len(plain) > 1 << 20: raise ValueError()
        return data, plain
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise _error() from None


def store_delivery(header, raw):
    if os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_INBOX') != '1':
        raise _error('native_inbox_disabled',404)
    from tenant_browser_calls import gateway_auth, _lock
    cfg = gateway_auth(header)  # Reuse current managed-source credentials.
    if cfg.get('managed') is not True: raise _error('native_source_denied',403)
    delivery, plain = validate(raw)
    scope, event = delivery['scope'], delivery['event']
    if (scope['tenant'],scope['source'],scope['account']) != (cfg['tenant_id'],cfg['channel_id'],cfg['account_jid']):
        raise _error('native_scope_mismatch',403)
    digest = hashlib.sha256(plain).hexdigest()
    with _connect() as conn, conn.cursor() as cur:
        # Same order as gateway sync/source actions. Revalidate the token and
        # live source in this transaction, rather than trusting an auth snapshot.
        _lock(cur,cfg)
        cur.execute('''SELECT s.gateway_id FROM saas_browser_call_sources s
          JOIN saas_connect_channels c ON c.tenant_id=s.tenant_id AND c.connect_channel_id=s.channel_id
          WHERE s.tenant_id=%s::uuid AND s.channel_id=%s::uuid AND s.gateway_id=%s::uuid
            AND s.account_jid=%s AND s.token_sha256=%s AND s.enabled=TRUE AND c.active=TRUE
          FOR SHARE OF s,c''',(scope['tenant'],scope['source'],cfg['gateway_id'],scope['account'],cfg['token_sha256']))
        if not cur.fetchone(): raise _error('native_source_changed',409)
        cur.execute('''SELECT stored_sequence FROM saas_whatsapp_capture_generations
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s
            AND gateway_id=%s::uuid AND account_jid=%s AND enabled=TRUE FOR UPDATE''',
          (scope['tenant'],scope['source'],scope['generation'],cfg['gateway_id'],scope['account']))
        capture = cur.fetchone()
        if not capture: raise _error('native_generation_unregistered',409)
        key = (scope['tenant'],scope['source'],scope['generation'])
        cur.execute('''SELECT sequence,after_sequence,payload_sha256 FROM saas_whatsapp_native_inbox
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s AND event_id=%s''',(*key,event['id']))
        previous = cur.fetchone()
        if previous:
            if (previous['sequence'] != event['sequence'] or previous['after_sequence'] != delivery['after']
                    or not hmac.compare_digest(previous['payload_sha256'],digest)):
                raise _error('native_event_conflict',409)
        else:
            if capture['stored_sequence'] != delivery['after']:
                raise _error('native_cursor_conflict',409)
            # Bind plaintext to scope before encryption; a future consumer must
            # verify this wrapper when decrypting, never just trust the row ID.
            sealed = _fernet().encrypt(json.dumps({'scope':scope,'event_id':event['id'],'payload':json.loads(plain)},
                sort_keys=True,separators=(',',':')).encode())
            cur.execute('''INSERT INTO saas_whatsapp_native_inbox
              (tenant_id,channel_id,generation,event_id,sequence,after_sequence,payload_sha256,encrypted_payload)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
              (*key,event['id'],event['sequence'],delivery['after'],digest,sealed))
            cur.execute('''UPDATE saas_whatsapp_capture_generations SET stored_sequence=%s
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s AND stored_sequence=%s''',
              (event['sequence'],*key,delivery['after']))
    # Context manager commit succeeded. Never acknowledge in a finally block or
    # before commit; an uncertain outcome is retried with the same event ID.
    return {'version':1,'scope':scope,'after':delivery['after'],'sequence':event['sequence'],
            'event_id':event['id'],'proof':delivery['proof'],'state':'stored'}
