"""Shared, server-owned monetary reservations for paid AI provider calls."""
import json
import math
import os
import uuid

from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect

MICRO = 1_000_000


def _json_env(name):
    try:
        value = json.loads(str(os.environ.get(name) or '{}'))
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def limit_microusd(tenant_id):
    """A missing tenant cap preserves current non-pilot behavior."""
    try:
        return max(0, int(_json_env('AI_TENANT_SPEND_LIMITS_MICROUSD').get(str(tenant_id))))
    except (TypeError, ValueError):
        return 0


def price(model):
    raw = _json_env('AI_MODEL_PRICES_MICROUSD_PER_MILLION').get(str(model))
    if not isinstance(raw, dict):
        return None
    try:
        incoming, outgoing = int(raw['input']), int(raw['output'])
        return (incoming, outgoing) if incoming >= 0 and outgoing >= 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_spend_reservations(
          tenant_id UUID NOT NULL, id UUID NOT NULL, call_key TEXT NOT NULL, model TEXT NOT NULL,
          kind TEXT NOT NULL, reserved_microusd BIGINT NOT NULL CHECK(reserved_microusd >= 0),
          actual_microusd BIGINT, state TEXT NOT NULL CHECK(state IN ('reserved','settled','unknown','released')),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ,
          PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,call_key))''')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_spend_reservations_tenant_idx ON saas_ai_spend_reservations(tenant_id,state)')


def _cost(prices, input_tokens, output_tokens):
    return (int(math.ceil(max(0, int(input_tokens)) * prices[0] / MICRO)) +
            int(math.ceil(max(0, int(output_tokens)) * prices[1] / MICRO)))


def _total_cost_upper(prices, total_tokens):
    """Use the higher token tariff when a caller has only a total bound."""
    return int(math.ceil(max(0, int(total_tokens)) * max(prices) / MICRO))


def reserve(cur, tenant_id, call_key, kind, model, input_upper=0, output_upper=0, *, total_upper=0):
    """Atomically reserve a conservative upper bound before a provider call.

    The per-tenant advisory lock serializes all four call paths. An existing
    call key is idempotent; an unknown or settled call can never be charged
    again. Unknown model price is fail-closed whenever a tenant cap exists.
    """
    cap = limit_microusd(tenant_id)
    if not cap:
        return None
    prices = price(model)
    if not prices:
        raise TenantLinearError('ai_spend_price_unknown', 409)
    amount = _total_cost_upper(prices, total_upper) if total_upper else _cost(prices, input_upper, output_upper)
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('tenant-ai-spend:' + str(tenant_id),))
    cur.execute('SELECT id,state FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key=%s FOR UPDATE',
                (tenant_id, str(call_key)[:200]))
    old = cur.fetchone()
    if old:
        if old['state'] in {'reserved', 'unknown', 'settled'}:
            return str(old['id'])
        raise TenantLinearError('ai_spend_reservation_released', 409)
    cur.execute('''SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_microusd
                    ELSE COALESCE(actual_microusd,reserved_microusd) END),0) AS used
                   FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid''', (tenant_id,))
    if int((cur.fetchone() or {}).get('used') or 0) + amount > cap:
        raise TenantLinearError('ai_spend_budget_exhausted', 429)
    reservation_id = str(uuid.uuid4())
    cur.execute('''INSERT INTO saas_ai_spend_reservations
                  (tenant_id,id,call_key,model,kind,reserved_microusd,state)
                  VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,'reserved')''',
                (tenant_id, reservation_id, str(call_key)[:200], str(model)[:100], str(kind)[:40], amount))
    return reservation_id


def settle(tenant_id, call_key, usage, unknown=False):
    """Settle confirmed tokens; ambiguity retains the entire reservation."""
    if not limit_microusd(tenant_id):
        return
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('tenant-ai-spend:' + str(tenant_id),))
            cur.execute('SELECT model,reserved_microusd,state FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key=%s FOR UPDATE',
                        (tenant_id, str(call_key)[:200]))
            row = cur.fetchone()
            if not row or row['state'] != 'reserved':
                conn.commit()
                return
            prices = price(row['model'])
            values = usage if isinstance(usage, dict) else {}
            if unknown or not prices:
                actual = None
            else:
                incoming = values.get('input_tokens')
                outgoing = values.get('output_tokens')
                total = values.get('total_tokens')
                if incoming is None or outgoing is None:
                    actual = _total_cost_upper(prices, total or 0)
                else:
                    actual = max(_cost(prices, incoming, outgoing), _total_cost_upper(prices, total or 0))
                # A provider report outside the preflight upper bound is not a
                # safe settlement. Retain the full reserve and block future work.
                if actual > int(row['reserved_microusd']):
                    actual = None
            if actual is None:
                cur.execute("UPDATE saas_ai_spend_reservations SET state='unknown',completed_at=now() WHERE tenant_id=%s::uuid AND call_key=%s",
                            (tenant_id, str(call_key)[:200]))
            else:
                cur.execute("UPDATE saas_ai_spend_reservations SET state='settled',actual_microusd=%s,completed_at=now() WHERE tenant_id=%s::uuid AND call_key=%s",
                            (actual, tenant_id, str(call_key)[:200]))
        conn.commit()


def release(tenant_id, call_key):
    """Release only a confirmed pre-provider cancellation."""
    if not limit_microusd(tenant_id):
        return
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('tenant-ai-spend:' + str(tenant_id),))
            cur.execute("UPDATE saas_ai_spend_reservations SET state='released',actual_microusd=0,completed_at=now() WHERE tenant_id=%s::uuid AND call_key=%s AND state='reserved'",
                        (tenant_id, str(call_key)[:200]))
        conn.commit()
