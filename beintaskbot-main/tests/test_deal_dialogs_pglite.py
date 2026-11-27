"""Verify exact deal routing and tenant/source isolation in PostgreSQL."""
import uuid

import pytest

import tenant_crm_native_deals
import tenant_deal_dialogs as linked
from tenant_kommo_board_provenance import digest, migrate
from tenant_crm_relation_identity import current_binding
from tenant_linear_policy import TenantLinearError
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, database, db


def test_native_uses_saved_route_within_actor_source_scope(db, monkeypatch):
    monkeypatch.setattr(linked, '_connect', lambda: db)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    deal_id = str(uuid.uuid4())
    fixture = {'channel_id': CA, 'conversation_id': 'client'}

    def get_deal(tenant, requested, *, actor_id):
        assert requested == deal_id
        assert actor_id in (11, 22, 33)
        return fixture if tenant == A else None

    monkeypatch.setattr(tenant_crm_native_deals, 'get_deal', get_deal)
    dialogs = linked.linked_dialogs(PROFILE, 'native', deal_id)['dialogs']
    assert [(row['lead_id'], row['channel_id']) for row in dialogs] == [(41, CA)]
    with pytest.raises(TenantLinearError) as foreign:
        linked.linked_dialogs({'tenant_id': B, 'telegram_id': 22}, 'native', deal_id)
    assert foreign.value.status == 404

    db.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
                  VALUES(%s::uuid,33,'operator','Employee','["dialogs"]'::jsonb)""", (A,))
    db.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
                  VALUES(%s::uuid,'members','{"33":{"access_scopes":{"dialogs":{"view":"self"}}}}'::jsonb)
                  ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value""", (A,))
    db.commit()
    employee = {'tenant_id': A, 'telegram_id': 33}
    assert linked.linked_dialogs(employee, 'native', deal_id) == {'dialogs': []}
    fixture['channel_id'] = CB
    assert linked.linked_dialogs(PROFILE, 'native', deal_id) == {'dialogs': []}


def test_kommo_uses_exact_lead_with_proof_for_the_current_connection(db, monkeypatch):
    monkeypatch.setattr(linked, '_connect', lambda: db)
    with db.cursor() as cur:
        migrate(cur)
        cur.execute("""INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain,connected_at)
                       VALUES(%s::uuid,'kommo','connected','one.kommo.com','2026-11-25T12:00:00Z')""", (A,))
        cur.execute("""INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,name,raw)
                       SELECT %s::uuid,i,'Identical title',jsonb_build_object('id',i)
                       FROM generate_series(1,100) i""", (A,))
        cur.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body)
                       VALUES(%s::uuid,%s::uuid,97,'km1','kommo','incoming','Kommo message')""",
                    (str(uuid.uuid4()), A))
        binding = current_binding(cur, A)
        cur.execute("""INSERT INTO saas_crm_board_provenance(tenant_id,resource,external_id,account_id,
                       connection_generation,raw_digest,source_revision)
                       VALUES (%s::uuid,'leads',97,%s,%s,%s,'verified')""",
                    (A, binding.account_id, binding.connection_generation, digest({'id': 97})))
    db.commit()
    assert [(row['provider'], row['lead_id']) for row in linked.linked_dialogs(PROFILE, 'kommo', '97')['dialogs']] == [('kommo',97)]
    db.execute('UPDATE saas_connect_conversations SET lead_id=97 WHERE tenant_id=%s::uuid', (A,))
    db.commit()
    assert linked.linked_dialogs(PROFILE, 'kommo', '97') == {'dialogs': []}
    db.execute('UPDATE saas_connect_conversations SET lead_id=41 WHERE tenant_id=%s::uuid', (A,))
    db.commit()
    assert linked.linked_dialogs(PROFILE, 'kommo', '99') == {'dialogs': []}
    db.execute("UPDATE saas_tenant_integrations SET account_domain='two.kommo.com' WHERE tenant_id=%s::uuid", (A,))
    db.commit()
    assert linked.linked_dialogs(PROFILE, 'kommo', '97') == {'dialogs': []}
