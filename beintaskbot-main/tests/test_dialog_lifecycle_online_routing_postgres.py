"""Availability routing through the existing queue on disposable PostgreSQL."""
from datetime import datetime, timedelta, timezone
import uuid

import pytest

import tenant_dialog_lifecycle as lifecycle
import tenant_dialog_presence as presence
from tenant_linear_policy import TenantLinearError
from test_connect_channel_multiprocess import database
from test_dialog_lifecycle_pglite import command, episodes, state
from test_tenant_support_inbox import A, CA, db


def test_four_state_routing_keeps_an_open_dialogue_until_the_next_inbound(episodes, monkeypatch):
    db = episodes
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV, 'true')
    with db.cursor() as cur:
        presence.provision_status_schema(cur)
    db.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
        VALUES(%s::uuid,34,'operator','Available operator','["dialogs"]'::jsonb)""", (A,))
    db.commit()
    with db.cursor() as cur:
        assert presence.is_online(cur,A,34,source_channel=CA)  # No saved choice defaults to Online.
        assert not presence.is_online(cur,A,34,source_channel=str(uuid.uuid4()))
        assert not presence.is_online(cur,A,11)  # Owner is not an operator.
    db.commit()
    accepted = command(actor=33)['lifecycle']
    presence.set_status({'tenant_id':A,'telegram_id':34},{'status':'lunch'})
    with pytest.raises(TenantLinearError) as unavailable:
        command('transfer',accepted['revision'],actor=33,target_employee_id=34)
    assert unavailable.value.status == 403
    presence.set_status({'tenant_id':A,'telegram_id':34},{'status':'online'})
    presence.set_status({'tenant_id':A,'telegram_id':33},{'status':'offline'})
    assert state(db)['operator_id'] == 33
    with db.cursor() as cur:
        lifecycle.require_access(cur,A,33,'connect',CA,'client')
        lifecycle.note_inbound(cur,A,CA,'client',str(uuid.uuid4()),datetime.now(timezone.utc)-timedelta(hours=1))
    db.commit()
    assert state(db)['operator_id'] == 33  # Delayed event is not a fresh handoff.
    with db.cursor() as cur:
        lifecycle.note_inbound(cur,A,CA,'client',str(uuid.uuid4()),datetime.now(timezone.utc)+timedelta(seconds=3))
    db.commit()
    waiting = state(db)
    assert waiting['state'] == 'waiting' and waiting['operator_id'] is None
    with db.cursor() as cur, pytest.raises(TenantLinearError) as invisible:
        lifecycle.require_access(cur,A,33,'connect',CA,'client')
    assert invisible.value.status == 404
    db.commit()
    with pytest.raises(TenantLinearError) as offline:
        command(actor=33,revision=waiting['revision'])
    assert offline.value.status == 403
    assert command(actor=34,revision=waiting['revision'])['lifecycle']['operator_id'] == 34
    old = db.execute("SELECT closed_at FROM saas_dialog_episodes WHERE tenant_id=%s::uuid AND id=%s::uuid",
                     (A, accepted['id']))
    assert old[0]['closed_at'] is not None
