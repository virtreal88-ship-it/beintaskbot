"""Dormant, crash-conservative driver. No production transport is registered.

An adapter MUST atomically compare the complete remote relation state and apply
one narrow request. Kommo's public link/unlink endpoints do not establish that
contract. A read/check/POST implementation is NOT a valid adapter. Tests supply
an in-memory conditional adapter only. BEIN has no relation adapter at all.
"""
import json
from tenant_crm_native_access import CRMAccessError
from tenant_crm_relation_contract import RelationContractError
from tenant_crm_relation_store import reauthorize
from tenant_crm_relation_kommo import CONTRACT_VERSION, RelationConflict, canonical, digest, plan


def _row(cur, event):
    cur.execute('SELECT * FROM saas_crm_relation_outbox WHERE id=%s::uuid FOR UPDATE', (event,))
    return cur.fetchone()


def _finish(cur, event, state, error='', observation=None):
    cur.execute('''UPDATE saas_crm_relation_outbox SET state=%s,error_code=%s,
      observation=%s::jsonb,updated_at=clock_timestamp() WHERE id=%s::uuid''',
      (state, error, json.dumps(observation) if observation is not None else None, event))
    return state


def drive(factory, event, *, adapter=None):
    """Process at most one step. No automatic retry or compensation.

    A session lock spans commits, while sending is committed BEFORE provider
    I/O. After a crash/timeout, sending is terminal unknown, even when a later
    GET shows the desired state: the earlier request may still be in flight.
    Connections MUST be dedicated, not pooled without resetting session locks.
    No caller exists in the app. adapter=None always fails closed without I/O.
    """
    with factory() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked',
                        ('crm-relation:' + str(event),))
            if not cur.fetchone()['locked']:
                return 'busy'
            conn.commit()
            try:
                row = _row(cur, event)
                if not row:
                    return 'missing'
                if row['state'] not in ('pending', 'processing', 'unknown'):
                    return row['state']
                if row['attempt_outcome'] == 'sending' or row['state'] == 'unknown':
                    return _finish(cur, event, 'unknown', 'relation_outcome_unknown')
                if adapter is None:
                    return _finish(cur, event, 'disabled', 'relation_conditional_adapter_unavailable')
                try:
                    intent = reauthorize(cur, row)
                    if intent.changed.binding.provider != 'kommo':
                        raise RelationConflict('relation_provider_unsupported')
                    expectation = intent.provider_expectation
                    if expectation is None or expectation.contract_version != CONTRACT_VERSION:
                        raise RelationConflict('relation_contract_missing')
                    # Adapter binds reads and writes to the exact connection generation.
                    observed = canonical(adapter.observe(intent.changed.binding, intent.relation,
                        intent.changed.external_id), intent.relation)
                    initial = row['plan']['initial'] if row['plan'] else observed
                    prepared = plan(intent.relation, intent.changed.external_id,
                        intent.before.external_id if intent.before else None,
                        intent.after.external_id if intent.after else None, initial)
                    if (digest(prepared.states[0], intent.relation) != expectation.expected_digest
                            or digest(prepared.states[-1], intent.relation) != expectation.desired_digest):
                        raise RelationConflict('relation_expectation_mismatch')
                    index = row['step_index']
                    if index > len(prepared.steps) or observed != prepared.states[index]:
                        raise RelationConflict('relation_remote_conflict')
                    if index == len(prepared.steps):
                        return _finish(cur, event, 'synced', observation=observed)
                    cur.execute('''UPDATE saas_crm_relation_outbox SET plan=%s::jsonb,
                      state='processing',attempt_outcome='sending',updated_at=clock_timestamp()
                      WHERE id=%s::uuid''', (json.dumps({'initial': initial}), event))
                    conn.commit()  # Durable no-replay barrier. No I/O precedes it.
                    row = _row(cur, event)
                    intent = reauthorize(cur, row)
                    # Keep authorization, resource, mapping, connection and sync policy
                    # row locks held throughout conditional I/O and the result commit.
                    try:
                        result = adapter.compare_and_apply(intent.changed.binding, intent.relation,
                            prepared.states[index], prepared.request(index))
                    except Exception:
                        return _finish(cur, event, 'unknown', 'relation_outcome_unknown')
                    if result == 'conflict':
                        return _finish(cur, event, 'conflict', 'relation_remote_conflict')
                    if result != 'applied':
                        return _finish(cur, event, 'unknown', 'relation_outcome_unknown')
                    try:
                        observed = canonical(adapter.observe(intent.changed.binding, intent.relation,
                            intent.changed.external_id), intent.relation)
                    except Exception:
                        return _finish(cur, event, 'unknown', 'relation_readback_unknown')
                    if observed != prepared.states[index + 1]:
                        return _finish(cur, event, 'conflict', 'relation_readback_conflict', observed)
                    cur.execute('''UPDATE saas_crm_relation_outbox SET step_index=step_index+1,
                      attempt_outcome='acknowledged' WHERE id=%s::uuid''', (event,))
                    return _finish(cur, event, 'synced' if index + 1 == len(prepared.steps) else 'pending',
                                   observation=observed)
                except CRMAccessError as error:
                    state = 'superseded' if error.code == 'relation_superseded' else 'disabled'
                    return _finish(cur, event, state, error.code)
                except (RelationConflict, RelationContractError):
                    return _finish(cur, event, 'conflict', 'relation_validation_conflict')
            finally:
                # A failed commit leaves the prior durable sending marker in place.
                try:
                    conn.commit()
                finally:
                    conn.rollback()
                    cur.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',
                                ('crm-relation:' + str(event),))
                    conn.commit()
