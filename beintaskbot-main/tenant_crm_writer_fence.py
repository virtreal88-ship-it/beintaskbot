"""Per-card local writer admission; never evidence of remote request completion."""


def card_key(tenant, entity, ident):
    import uuid
    tenant, ident = str(uuid.UUID(str(tenant))), str(uuid.UUID(str(ident)))
    return f'crm-card:{tenant}:{entity}:{ident}'


def lock_card(cur, tenant, entity, ident):
    # Always before tenant/member/native row locks. Held until mutation commit.
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                (card_key(tenant, entity, ident),))


def claim_not_started(prior, attempt):
    return bool(prior and prior['outcome']=='not_started'
                and prior['claim_attempt']==attempt)


def legacy_uncertain(cur, tenant, entity, ident):
    # Old workers did not persist possible-send evidence. An attempted pending
    # event can be a timed-out linked PATCH; lease expiry is not cancellation.
    cur.execute('''SELECT 1 FROM saas_crm_sync_outbox o
      LEFT JOIN saas_crm_legacy_delivery_attempts a ON a.event_id=o.id
      WHERE o.tenant_id=%s::uuid AND o.entity_type=%s AND o.internal_id=%s::uuid
      AND (a.outcome IN ('sending','unknown') OR o.state='unknown'
        OR ((o.state='processing' OR (o.state='pending' AND o.attempts>0))
          AND NOT COALESCE(a.outcome='not_started' AND a.claim_attempt=o.attempts,FALSE)))
      LIMIT 1''', (tenant, entity, ident))
    return bool(cur.fetchone())
