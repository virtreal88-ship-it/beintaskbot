"""Narrow role override for a verified employee playing the scoped pilot client."""
from unittest.mock import patch

import tenant_ai_pilot as pilot
import tenant_connect_transport as transport

TENANT = pilot.SCOPED_LIVE_TENANT
CHANNEL = pilot.SCOPED_LIVE_CHANNEL
CUSTOMER = '994102135103@s.whatsapp.net'
GROUP = pilot.SCOPED_LIVE_GROUP
ENV = {
    'AI_AGENTS_PILOT_TENANT_ID': TENANT,
    'AI_AGENTS_PILOT_CHANNEL_ID': CHANNEL,
    'AI_AGENTS_PILOT_CONVERSATION_ID': CUSTOMER,
    'AI_AGENTS_PILOT_GROUP_CONVERSATION_ID': GROUP,
    'AI_AGENTS_SCOPED_LIVE_ENABLED': 'true',
    'AI_AGENTS_PILOT_SCOPE_ENABLED': 'false',
    'AI_AGENTS_ENABLED': 'true',
}


class Cursor:
    def __init__(self, valid=True):
        self.valid = valid
        self.sql = ''
        self.params = None
    def execute(self, sql, params=None):
        self.sql, self.params = sql, params
    def fetchone(self):
        return {'valid': self.valid}


def active_snapshot(**changes):
    row = {
        'conversation_state': 'active', 'conversation_ai_enabled': True,
        'conversation_manual_hold': False, 'active': True,
        'integration_status': 'connected', 'tenant_status': 'active',
        'company_enabled': True, 'emergency_stop': False,
        'agent_id': pilot.SCOPED_LIVE_AGENT, 'agent_enabled': True,
        'agent_mode': 'manual', 'exact_agent_scope': True,
    }
    row.update(changes)
    return row


def install_valid_gate(monkeypatch, row=None):
    monkeypatch.setattr(pilot, '_lock_scope', lambda *_: True)
    monkeypatch.setattr(pilot, '_snapshot', lambda *_: (row or active_snapshot(), 'scope-fingerprint'))


def test_verified_employee_in_exact_live_scope_can_be_test_client(monkeypatch):
    install_valid_gate(monkeypatch)
    cur = Cursor(True)
    with patch.dict('os.environ', ENV, clear=True):
        assert transport._pilot_test_client_role_override(
            cur, tenant=TENANT, channel_id=CHANNEL, conversation=CUSTOMER,
            employee=115, outbound=False, coordinated=False, identity_conflict=False,
            occurred_at='2026-10-30T20:12:00Z')
    assert 'owner_expires_at>clock_timestamp()' in cur.sql
    assert cur.params[0] == cur.params[1] == '2026-10-30T20:12:00+00:00'


def test_employee_outside_exact_scope_remains_human(monkeypatch):
    called = []
    monkeypatch.setattr(pilot, 'test_client_override_allowed', lambda *args: called.append(args) or True)
    with patch.dict('os.environ', ENV, clear=True):
        assert not transport._pilot_test_client_role_override(
            Cursor(), tenant=TENANT, channel_id=CHANNEL, conversation='994707010207@s.whatsapp.net',
            employee=115, outbound=False, coordinated=False, identity_conflict=False,
            occurred_at='2026-10-30T20:12:00Z')
    assert called == []


def test_outgoing_employee_message_is_never_a_test_client(monkeypatch):
    called = []
    monkeypatch.setattr(pilot, 'test_client_override_allowed', lambda *args: called.append(args) or True)
    with patch.dict('os.environ', ENV, clear=True):
        assert not transport._pilot_test_client_role_override(
            Cursor(), tenant=TENANT, channel_id=CHANNEL, conversation=CUSTOMER,
            employee=115, outbound=True, coordinated=False, identity_conflict=False,
            occurred_at='2026-10-30T20:12:00Z')
    assert called == []


def test_pause_or_expired_attestation_denies_employee_override(monkeypatch):
    install_valid_gate(monkeypatch, active_snapshot(conversation_state='human'))
    with patch.dict('os.environ', ENV, clear=True):
        assert not pilot.test_client_override_allowed(
            Cursor(True), TENANT, CHANNEL, CUSTOMER, '2026-10-30T20:12:00Z')
    install_valid_gate(monkeypatch)
    with patch.dict('os.environ', ENV, clear=True):
        assert not pilot.test_client_override_allowed(
            Cursor(False), TENANT, CHANNEL, CUSTOMER, '2026-10-30T20:12:00Z')


def test_missing_provider_timestamp_never_activates_employee_override(monkeypatch):
    install_valid_gate(monkeypatch)
    with patch.dict('os.environ', ENV, clear=True):
        assert not pilot.test_client_override_allowed(Cursor(True),TENANT,CHANNEL,CUSTOMER,None)
        assert not pilot.test_client_override_allowed(Cursor(True),TENANT,CHANNEL,CUSTOMER,'')


def test_override_is_disabled_after_scoped_pause_or_legacy_guard_arming(monkeypatch):
    install_valid_gate(monkeypatch)
    with patch.dict('os.environ', {**ENV, 'AI_AGENTS_SCOPED_LIVE_ENABLED': 'false'}, clear=True):
        assert not pilot.test_client_override_allowed(
            Cursor(True), TENANT, CHANNEL, CUSTOMER, '2026-10-30T20:12:00Z')
    with patch.dict('os.environ', {**ENV, 'AI_AGENTS_PILOT_SCOPE_ENABLED': 'true'}, clear=True):
        assert not pilot.test_client_override_allowed(
            Cursor(True), TENANT, CHANNEL, CUSTOMER, '2026-10-30T20:12:00Z')
