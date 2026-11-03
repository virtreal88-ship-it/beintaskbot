"""DISPOSABLE LOCAL PROTOTYPE. Never connect to production or call a provider.

This records conservative response reservations. It is not authentication,
provider admission, or a deployed global spending limit.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

CAP_MICROUSD = 5_000_000
QUOTE_VERSION = 'synthetic-mini-2026-11-03-v1'
MODEL = 'gpt-realtime-2.1-mini'
# Max model context/output * highest listed modality tariff + 10% uplift.
# One response, no transcription, tools, reconnect or other paid operation.
RESERVE_MICROUSD = (128_000 * 10 + 32_000 * 20) * 11 // 10
QUOTE_EXPIRES = datetime(2026, 11, 4, tzinfo=timezone.utc)
TEST_DB = re.compile(r'^test_voice_budget_[0-9a-f]{32}$')
EXECUTOR = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

SCHEMA = '''
CREATE TABLE poc_voice_budget (
 id SMALLINT PRIMARY KEY CHECK (id = 1),
 cap_microusd BIGINT NOT NULL CHECK (cap_microusd = 5000000),
 frozen BOOLEAN NOT NULL DEFAULT false
);
INSERT INTO poc_voice_budget(id,cap_microusd) VALUES(1,5000000);
CREATE TABLE poc_voice_reservations (
 request_id UUID PRIMARY KEY, tenant_id UUID NOT NULL, session_id UUID NOT NULL,
 actor_id BIGINT NOT NULL, response_number INTEGER NOT NULL CHECK(response_number BETWEEN 1 AND 2),
 executor TEXT NOT NULL, quote_version TEXT NOT NULL, model TEXT NOT NULL,
 fingerprint TEXT NOT NULL, reserved_microusd BIGINT NOT NULL CHECK(reserved_microusd > 0),
 actual_microusd BIGINT CHECK(actual_microusd IS NULL OR actual_microusd >= 0),
 state TEXT NOT NULL CHECK(state IN ('reserved','claimed','unknown','settled','released')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,session_id,response_number)
);
'''


def local_connect(dsn):
    """Reject remote hosts and URL parameters, including pg host overrides."""
    parsed = urlsplit(dsn)
    if parsed.scheme not in ('postgres', 'postgresql') or parsed.query or parsed.fragment:
        raise ValueError('local_test_database_only')
    args = conninfo_to_dict(dsn)
    if (parsed.hostname not in ('localhost', '127.0.0.1', '::1') or
        args.get('host') not in ('localhost', '127.0.0.1', '::1') or
        not TEST_DB.fullmatch(parsed.path.removeprefix('/'))):
        raise ValueError('local_test_database_only')
    return psycopg.connect(dsn, row_factory=dict_row)


def _id(value):
    if not isinstance(value, str):
        raise ValueError('invalid_uuid')
    return str(uuid.UUID(value))


def _scope(scope):
    if not isinstance(scope, dict) or set(scope) != {'request_id','tenant_id','session_id','actor_id','response_number','executor'}:
        raise ValueError('invalid_scope')
    result = dict(scope)
    for key in ('request_id','tenant_id','session_id'):
        result[key] = _id(result[key])
    if type(result['actor_id']) is not int or not (1 <= result['actor_id'] < 2**63):
        raise ValueError('invalid_scope')
    if type(result['response_number']) is not int or result['response_number'] not in (1,2):
        raise ValueError('invalid_scope')
    if not isinstance(result['executor'], str) or not EXECUTOR.fullmatch(result['executor']):
        raise ValueError('invalid_scope')
    return result


def _fingerprint(scope):
    payload = {'scope': scope, 'model': MODEL, 'quote': QUOTE_VERSION, 'reserved_microusd': RESERVE_MICROUSD}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Ledger:
    def __init__(self, dsn):
        self.dsn = dsn
        # Validate before any operation; connection itself is never to production.
        with local_connect(dsn):
            pass

    def reserve(self, scope):
        s = _scope(scope)
        if datetime.now(timezone.utc) >= QUOTE_EXPIRES:
            raise ValueError('quote_expired')
        fingerprint = _fingerprint(s)
        with local_connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('SELECT cap_microusd,frozen FROM poc_voice_budget WHERE id=1 FOR UPDATE')
            budget = cur.fetchone()
            if not budget or budget['cap_microusd'] != CAP_MICROUSD or budget['frozen']:
                raise ValueError('budget_frozen_or_invalid')
            cur.execute('SELECT * FROM poc_voice_reservations WHERE request_id=%s', (s['request_id'],))
            previous = cur.fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise ValueError('request_scope_conflict')
                if previous['state'] == 'released':
                    raise ValueError('reservation_released')
                return previous
            cur.execute('SELECT request_id FROM poc_voice_reservations WHERE tenant_id=%s AND session_id=%s AND response_number=%s',
                        (s['tenant_id'],s['session_id'],s['response_number']))
            if cur.fetchone():
                raise ValueError('response_already_reserved')
            cur.execute("SELECT COALESCE(sum(GREATEST(reserved_microusd,COALESCE(actual_microusd,0))),0) AS used FROM poc_voice_reservations WHERE state <> 'released'")
            if int(cur.fetchone()['used']) + RESERVE_MICROUSD > CAP_MICROUSD:
                raise ValueError('budget_exhausted')
            cur.execute('''INSERT INTO poc_voice_reservations(request_id,tenant_id,session_id,actor_id,response_number,executor,
              quote_version,model,fingerprint,reserved_microusd,state) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reserved') RETURNING *''',
              (s['request_id'],s['tenant_id'],s['session_id'],s['actor_id'],s['response_number'],s['executor'],QUOTE_VERSION,MODEL,fingerprint,RESERVE_MICROUSD))
            return cur.fetchone()

    def _transition(self, scope, expected, target):
        s = _scope(scope)
        with local_connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('''UPDATE poc_voice_reservations SET state=%s,updated_at=now() WHERE request_id=%s AND fingerprint=%s
              AND state=%s RETURNING *''', (target,s['request_id'],_fingerprint(s),expected))
            return cur.fetchone()

    def claim(self, scope):
        # Claim must commit before any hypothetical provider action. Reclaiming
        # a claimed/unknown/settled intent is forbidden, including on restart.
        return self._transition(scope, 'reserved', 'claimed')

    def cancel_before_claim(self, scope):
        return self._transition(scope, 'reserved', 'released')

    def mark_unknown(self, scope):
        return self._transition(scope, 'claimed', 'unknown')

    def note_actual(self, scope, actual_microusd):
        """Record a synthetic result, but never refund the conservative hold."""
        s = _scope(scope)
        if type(actual_microusd) is not int or not (0 <= actual_microusd < 2**63):
            raise ValueError('invalid_actual')
        with local_connect(self.dsn) as conn, conn.cursor() as cur:
            # Same lock order as reserve. Unexpected overrun freezes new admissions.
            cur.execute('SELECT id FROM poc_voice_budget WHERE id=1 FOR UPDATE')
            if not cur.fetchone():
                raise ValueError('budget_missing')
            cur.execute('''UPDATE poc_voice_reservations SET state='settled',actual_microusd=%s,updated_at=now()
              WHERE request_id=%s AND fingerprint=%s AND state IN ('claimed','unknown') RETURNING *''',
              (actual_microusd,s['request_id'],_fingerprint(s)))
            row = cur.fetchone()
            if row and actual_microusd > row['reserved_microusd']:
                cur.execute('UPDATE poc_voice_budget SET frozen=true WHERE id=1')
            return row
