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


def media_price(model, operation):
    """Return an allowlisted, fixed worst-case price for one media call.

    Media APIs do not consistently return token usage.  They therefore cannot
    share the text-token tariff.  Operations are admitted only when Railway
    has an explicit non-secret tariff for the exact configured model and
    operation (``asr`` or ``vision_low``); no code default can spend money.
    """
    if operation not in {'asr', 'vision_low'}:
        return None
    raw = _json_env('AI_MEDIA_OPERATION_PRICES_MICROUSD').get(str(model))
    if not isinstance(raw, dict):
        return None
    try:
        value = int(raw.get(operation))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_spend_reservations(
          tenant_id UUID NOT NULL, id UUID NOT NULL, call_key TEXT NOT NULL, model TEXT NOT NULL,
          kind TEXT NOT NULL, reserved_microusd BIGINT NOT NULL CHECK(reserved_microusd >= 0),
          media_operation TEXT, media_unit_microusd BIGINT, media_units INTEGER,
          campaign_id UUID, grant_id UUID,
          actual_microusd BIGINT, state TEXT NOT NULL CHECK(state IN ('reserved','settled','unknown','released')),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ,
          PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,call_key))''')
        # These fields pin a media tariff at admission.  Settlement must never
        # consult a later mutable runtime price configuration.
        cur.execute('ALTER TABLE saas_ai_spend_reservations ADD COLUMN IF NOT EXISTS media_operation TEXT')
        cur.execute('ALTER TABLE saas_ai_spend_reservations ADD COLUMN IF NOT EXISTS media_unit_microusd BIGINT')
        cur.execute('ALTER TABLE saas_ai_spend_reservations ADD COLUMN IF NOT EXISTS media_units INTEGER')
        cur.execute('ALTER TABLE saas_ai_spend_reservations ADD COLUMN IF NOT EXISTS campaign_id UUID')
        cur.execute('ALTER TABLE saas_ai_spend_reservations ADD COLUMN IF NOT EXISTS grant_id UUID')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_spend_reservations_tenant_idx ON saas_ai_spend_reservations(tenant_id,state)')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_spend_reservations_grant_idx ON saas_ai_spend_reservations(tenant_id,grant_id,state)')


def _cost(prices, input_tokens, output_tokens):
    return (int(math.ceil(max(0, int(input_tokens)) * prices[0] / MICRO)) +
            int(math.ceil(max(0, int(output_tokens)) * prices[1] / MICRO)))


def _total_cost_upper(prices, total_tokens):
    """Use the higher token tariff when a caller has only a total bound."""
    return int(math.ceil(max(0, int(total_tokens)) * max(prices) / MICRO))


def _grant_admit(cur, tenant_id, campaign_id, amount):
    if not campaign_id:
        return
    cur.execute('''SELECT g.id,g.status,g.cap_microusd FROM saas_ai_media_grants g
      JOIN saas_knowledge_campaigns c ON c.tenant_id=g.tenant_id AND c.id=g.campaign_id
      WHERE g.tenant_id=%s::uuid AND g.campaign_id=%s::uuid AND g.id=%s::uuid
        AND g.status='active' AND c.status='running' AND c.approved_at IS NOT NULL
      FOR UPDATE OF g''',(tenant_id,campaign_id,campaign_id))
    grant=cur.fetchone()
    if not grant:
        raise TenantLinearError('ai_media_grant_inactive',409)
    cur.execute('''SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_microusd
      ELSE COALESCE(actual_microusd,reserved_microusd) END),0) AS used FROM saas_ai_spend_reservations
      WHERE tenant_id=%s::uuid AND grant_id=%s::uuid''',(tenant_id,grant['id']))
    if int((cur.fetchone() or {}).get('used') or 0)+int(amount)>int(grant['cap_microusd']):
        raise TenantLinearError('ai_media_grant_budget_exhausted',429)


def reserve(cur, tenant_id, call_key, kind, model, input_upper=0, output_upper=0, *, total_upper=0, campaign_id=None):
    """Atomically reserve a conservative upper bound before a provider call.

    The per-tenant advisory lock serializes all four call paths. An existing
    call key is idempotent; an unknown or settled call can never be charged
    again. Unknown model price is fail-closed whenever a tenant cap exists.
    """
    cap = limit_microusd(tenant_id)
    if not cap and not campaign_id:
        return None
    prices = price(model)
    if not prices:
        raise TenantLinearError('ai_spend_price_unknown', 409)
    amount = _total_cost_upper(prices, total_upper) if total_upper else _cost(prices, input_upper, output_upper)
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('tenant-ai-spend:' + str(tenant_id),))
    cur.execute('SELECT id,state,campaign_id,grant_id FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key=%s FOR UPDATE',
                (tenant_id, str(call_key)[:200]))
    old = cur.fetchone()
    if old:
        if campaign_id and (str(old.get('campaign_id') or '')!=str(campaign_id) or str(old.get('grant_id') or '')!=str(campaign_id)):
            raise TenantLinearError('ai_spend_reservation_scope_mismatch',409)
        if old['state'] in {'reserved', 'unknown', 'settled'}:
            return str(old['id'])
        raise TenantLinearError('ai_spend_reservation_released', 409)
    cur.execute('''SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_microusd
                    ELSE COALESCE(actual_microusd,reserved_microusd) END),0) AS used
                   FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid''', (tenant_id,))
    if cap and int((cur.fetchone() or {}).get('used') or 0) + amount > cap:
        raise TenantLinearError('ai_spend_budget_exhausted', 429)
    _grant_admit(cur,tenant_id,campaign_id,amount)
    reservation_id = str(uuid.uuid4())
    cur.execute('''INSERT INTO saas_ai_spend_reservations
                  (tenant_id,id,call_key,model,kind,reserved_microusd,campaign_id,grant_id,state)
                  VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s::uuid,%s::uuid,'reserved')''',
                (tenant_id, reservation_id, str(call_key)[:200], str(model)[:100], str(kind)[:40], amount,campaign_id,campaign_id))
    return reservation_id


def reserve_media(cur, tenant_id, call_key, kind, model, operation, units, *, campaign_id=None):
    """Reserve a fixed, configured upper bound before a media provider call."""
    cap = limit_microusd(tenant_id)
    if not cap and not campaign_id:
        return None
    if type(units) is not int or units < 1:
        raise TenantLinearError('ai_spend_media_units_invalid', 409)
    unit = media_price(model, operation)
    if unit is None:
        raise TenantLinearError('ai_spend_media_price_unknown', 409)
    amount = unit * units
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('tenant-ai-spend:' + str(tenant_id),))
    cur.execute('SELECT id,state,campaign_id,grant_id FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key=%s FOR UPDATE',
                (tenant_id, str(call_key)[:200]))
    old = cur.fetchone()
    if old:
        if campaign_id and (str(old.get('campaign_id') or '')!=str(campaign_id) or str(old.get('grant_id') or '')!=str(campaign_id)):
            raise TenantLinearError('ai_spend_reservation_scope_mismatch',409)
        if old['state'] in {'reserved', 'unknown', 'settled'}:
            return str(old['id'])
        raise TenantLinearError('ai_spend_reservation_released', 409)
    cur.execute('''SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_microusd
                    ELSE COALESCE(actual_microusd,reserved_microusd) END),0) AS used
                   FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid''', (tenant_id,))
    if cap and int((cur.fetchone() or {}).get('used') or 0) + amount > cap:
        raise TenantLinearError('ai_spend_budget_exhausted', 429)
    _grant_admit(cur,tenant_id,campaign_id,amount)
    reservation_id = str(uuid.uuid4())
    cur.execute('''INSERT INTO saas_ai_spend_reservations
                  (tenant_id,id,call_key,model,kind,reserved_microusd,media_operation,media_unit_microusd,media_units,campaign_id,grant_id,state)
                  VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s::uuid,%s::uuid,'reserved')''',
                (tenant_id, reservation_id, str(call_key)[:200], str(model)[:100], str(kind)[:40], amount,
                 operation, unit, units,campaign_id,campaign_id))
    return reservation_id


def settle_media(tenant_id, call_key, *, operation, attempted_units=0, reserved_units=0, unknown=False):
    """Settle only known completed media calls; ambiguity retains the reserve."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('tenant-ai-spend:' + str(tenant_id),))
            cur.execute('''SELECT reserved_microusd,state,media_operation,media_unit_microusd,media_units
                           FROM saas_ai_spend_reservations
                           WHERE tenant_id=%s::uuid AND call_key=%s FOR UPDATE''',
                        (tenant_id, str(call_key)[:200]))
            row = cur.fetchone()
            if not row or row['state'] != 'reserved':
                conn.commit(); return
            # A pre-existing reservation without immutable media metadata is
            # deliberately quarantined as unknown rather than re-priced from
            # the current environment.
            try:
                unit=int(row.get('media_unit_microusd'))
                planned_units=int(row.get('media_units'))
            except (TypeError, ValueError):
                unit=planned_units=0
            valid_plan=(row.get('media_operation')==operation and unit>0 and planned_units>0
                        and (not reserved_units or int(reserved_units)==planned_units))
            actual = (None if unknown or not valid_plan
                      else unit * max(0, int(attempted_units or 0)))
            if actual is None or actual > int(row['reserved_microusd']):
                cur.execute("UPDATE saas_ai_spend_reservations SET state='unknown',completed_at=now() WHERE tenant_id=%s::uuid AND call_key=%s",
                            (tenant_id, str(call_key)[:200]))
            elif not attempted_units:
                cur.execute("UPDATE saas_ai_spend_reservations SET state='released',actual_microusd=0,completed_at=now() WHERE tenant_id=%s::uuid AND call_key=%s",
                            (tenant_id, str(call_key)[:200]))
            else:
                cur.execute("UPDATE saas_ai_spend_reservations SET state='settled',actual_microusd=%s,completed_at=now() WHERE tenant_id=%s::uuid AND call_key=%s",
                            (actual, tenant_id, str(call_key)[:200]))
        conn.commit()


def settle(tenant_id, call_key, usage, unknown=False, *, reserved_prices=None, campaign_id=None):
    """Settle confirmed tokens; ambiguity retains the entire reservation."""
    if not limit_microusd(tenant_id) and not campaign_id:
        return
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('tenant-ai-spend:' + str(tenant_id),))
            cur.execute('SELECT model,reserved_microusd,state,campaign_id FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key=%s FOR UPDATE',
                        (tenant_id, str(call_key)[:200]))
            row = cur.fetchone()
            if (not row or row['state'] != 'reserved'
                or campaign_id and str(row.get('campaign_id') or '')!=str(campaign_id)):
                conn.commit()
                return
            prices = reserved_prices if reserved_prices is not None else price(row['model'])
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


def release(tenant_id, call_key, *, campaign_id=None):
    """Release only a confirmed pre-provider cancellation."""
    if not limit_microusd(tenant_id) and not campaign_id:
        return
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('tenant-ai-spend:' + str(tenant_id),))
            cur.execute("UPDATE saas_ai_spend_reservations SET state='released',actual_microusd=0,completed_at=now() WHERE tenant_id=%s::uuid AND call_key=%s AND state='reserved' AND (%s OR campaign_id=%s::uuid)",
                        (tenant_id, str(call_key)[:200],bool(limit_microusd(tenant_id)),campaign_id))
        conn.commit()
