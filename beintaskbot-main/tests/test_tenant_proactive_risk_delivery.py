"""Isolated PostgreSQL-compatible checks for owner-only risk alert delivery."""
from datetime import datetime, timedelta, timezone
import json
import hashlib
from pathlib import Path
import sys
from unittest.mock import patch
import uuid

import pytest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

from test_tenant_proactive_risk_pglite import PGliteDatabase
import tenant_notifications
from tenant_policy import TenantPolicy
from tenant_push_payload import payload_for
import tenant_proactive_risk_delivery as delivery
import tenant_proactive_risk_schema as risk_schema
from tenant_proactive_risk_policy import analyze_crm_task, crm_scope
from tenant_proactive_risk_store import replace_source_findings
import tenant_proactive_risk_store as risk_store


TENANT = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
OTHER_TENANT = 'bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb'
OWNER = 101
EMPLOYEE = 202
NOW = datetime.now(timezone.utc).replace(microsecond=0)
CONNECTED = NOW - timedelta(days=5)
SOURCE_ID = '509'


class TestRiskAlertPostgres:
    @pytest.fixture(autouse=True)
    def setup_database(self):
        database = PGliteDatabase()
        self.db = database
        self.preferences = {OWNER: {'internal': True, 'push': True}}
        self.workflow_patch = patch.object(delivery, '_read_tenant_workflow', side_effect=self.read_workflow)
        self.connect_patch = patch.object(delivery, '_connect', return_value=database)
        self.schema_patch = patch.object(delivery, 'ensure_schema', lambda _conn: None)
        self.workflow_patch.start(); self.connect_patch.start(); self.schema_patch.start()
        risk_schema._ready = False
        ensure_patch = patch.object(risk_schema, '_ensure_schema', lambda _conn: None)
        ensure_patch.start()
        database.execute('''CREATE TABLE saas_tenants(
          id UUID PRIMARY KEY,name TEXT NOT NULL,owner_telegram_id BIGINT NOT NULL,
          status TEXT NOT NULL,modules JSONB NOT NULL DEFAULT '{}',
          notification_rules JSONB NOT NULL DEFAULT '{}',onboarding JSONB NOT NULL DEFAULT '{}')''')
        risk_schema.ensure_schema(database)
        ensure_patch.stop()
        self.create_tables()
        self.seed_company(TENANT, OWNER)
        self.seed_company(OTHER_TENANT, 303)
        self.seed_source(TENANT, OWNER, SOURCE_ID)
        yield
        self.workflow_patch.stop(); self.connect_patch.stop(); self.schema_patch.stop()
        risk_schema._ready = False
        database.close()

    def read_workflow(self, cur, tenant):
        from tenant_platform import _read_tenant_workflow as original
        actor = OWNER if tenant == TENANT else 303
        return {'policies': {'members': {str(actor): {'notifications': {
            'proactive_risk': dict(self.preferences.get(actor, {}))}}}}}

    def create_tables(self):
        self.db.execute('''CREATE TABLE saas_tenant_members(
          tenant_id UUID NOT NULL,telegram_id BIGINT NOT NULL,role TEXT NOT NULL,active BOOLEAN NOT NULL,
          display_name TEXT NOT NULL DEFAULT '',PRIMARY KEY(tenant_id,telegram_id))''')
        self.db.execute('''CREATE TABLE saas_tenant_integrations(
          tenant_id UUID NOT NULL,provider TEXT NOT NULL,status TEXT NOT NULL,
          account_domain TEXT,connected_at TIMESTAMPTZ,PRIMARY KEY(tenant_id,provider))''')
        self.db.execute('''CREATE TABLE saas_crm_tasks(
          tenant_id UUID NOT NULL,kommo_task_id BIGINT NOT NULL,kommo_lead_id BIGINT NOT NULL DEFAULT 0,
          text TEXT NOT NULL,due_at TIMESTAMPTZ,responsible_id BIGINT NOT NULL DEFAULT 0,
          completed BOOLEAN NOT NULL,raw JSONB NOT NULL DEFAULT '{}',synced_at TIMESTAMPTZ NOT NULL,
          PRIMARY KEY(tenant_id,kommo_task_id))''')
        self.db.execute('''CREATE TABLE saas_tenant_notifications(
          id UUID PRIMARY KEY,tenant_id UUID NOT NULL,telegram_id BIGINT NOT NULL,event_key TEXT NOT NULL,
          title TEXT NOT NULL,body TEXT NOT NULL,target_url TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),read_at TIMESTAMPTZ)''')
        self.db.execute('''CREATE TABLE saas_push_devices(
          endpoint_hash TEXT PRIMARY KEY,owner_telegram_id BIGINT NOT NULL,subscription TEXT NOT NULL)''')
        self.db.execute('''CREATE TABLE saas_member_push_devices(
          tenant_id UUID NOT NULL,telegram_id BIGINT NOT NULL,endpoint_hash TEXT NOT NULL,
          label TEXT NOT NULL DEFAULT '',active BOOLEAN NOT NULL DEFAULT TRUE,updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,telegram_id,endpoint_hash))''')

    def seed_company(self, tenant, owner):
        self.db.execute('''INSERT INTO saas_tenants(id,name,owner_telegram_id,status)
          VALUES(%s::uuid,%s,%s,'active')''', (tenant, 'Example', owner))
        self.db.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,active)
          VALUES(%s::uuid,%s,'owner',TRUE)''', (tenant, owner))
        self.db.execute('''INSERT INTO saas_tenant_integrations
          (tenant_id,provider,status,account_domain,connected_at)
          VALUES(%s::uuid,'kommo','connected','example.kommo.com',%s)''', (tenant, CONNECTED))

    def task(self, source_id=SOURCE_ID):
        return {'tenant_id': TENANT, 'kommo_task_id': int(source_id), 'kommo_lead_id': 0,
                'text': 'Change price without permission', 'due_at': NOW - timedelta(days=1),
                'responsible_id': 0, 'completed': False, 'raw': {}, 'synced_at': NOW}

    def seed_source(self, tenant, actor, source_id):
        task = self.task(source_id)
        self.db.execute('''INSERT INTO saas_crm_tasks
          (tenant_id,kommo_task_id,text,due_at,responsible_id,completed,raw,synced_at)
          VALUES(%s::uuid,%s,%s,%s,%s,%s,%s::jsonb,%s)''',
          (tenant, int(source_id), task['text'], task['due_at'], task['responsible_id'],
           task['completed'], json.dumps(task['raw']), task['synced_at']))
        endpoint = self.endpoint_hash(tenant, source_id)
        self.db.execute('INSERT INTO saas_push_devices(endpoint_hash,owner_telegram_id,subscription) VALUES(%s,%s,%s)',
                        (endpoint, actor, 'encrypted-subscription'))
        self.db.execute('''INSERT INTO saas_member_push_devices(tenant_id,telegram_id,endpoint_hash,active)
          VALUES(%s::uuid,%s,%s,TRUE)''', (tenant, actor, endpoint))

    @staticmethod
    def endpoint_hash(tenant, source_id):
        prefix = 'a' if tenant == TENANT else 'b'
        return prefix * 56 + hashlib.sha256(str(source_id).encode()).hexdigest()[:8]

    def candidate(self, source_id=SOURCE_ID):
        task = self.task(source_id)
        return analyze_crm_task(task, checked_at=NOW)

    def record(self, tenant=TENANT, source_id=SOURCE_ID, findings=None, previous=None):
        findings = self.candidate(source_id) if findings is None else findings
        with self.db.cursor() as cur:
            replace_source_findings(cur, tenant, 'crm_task', source_id, 'v1',
                'CRM task '+source_id, findings, source_scope=crm_scope('example.kommo.com', CONNECTED),
                source_checked_at=NOW)

    def query(self, sql, params=()):
        return self.db.execute(sql, params)

    def test_owner_opt_in_generates_one_bell_and_only_owner_device_push(self):
        findings = self.candidate()
        assert findings and delivery.alertable(findings[0])
        self.record()
        self.record()  # A repeated observation of the same finding is deduplicated.
        assert self.query("SELECT state FROM saas_proactive_risk_alert_events WHERE tenant_id=%s::uuid", (TENANT,))[0]['state'] == 'pending'
        assert len(self.query('SELECT id FROM saas_proactive_risk_alert_events WHERE tenant_id=%s::uuid', (TENANT,))) == 1
        assert delivery.expand_events() == 1
        self.assertBellAndPush()
        assert delivery.expand_events() == 0
        assert len(self.query('SELECT id FROM saas_tenant_notifications')) == 1
        item = delivery.claim_push()
        assert item and item['send'] is True and item['recipient_id'] == OWNER
        assert item['endpoint_hash'] == self.endpoint_hash(TENANT, SOURCE_ID)
        delivery.finish_push(item, 'unknown')
        assert delivery.claim_push() is None
        assert self.query("SELECT status FROM saas_proactive_risk_alert_deliveries WHERE channel='push'")[0]['status'] == 'unknown'

    def test_opt_out_creates_no_event_and_stale_source_cancels_pending(self):
        self.preferences[OWNER] = {}
        self.record()
        assert self.query('SELECT id FROM saas_proactive_risk_alert_events') == []
        self.preferences[OWNER] = {'internal': True, 'push': True}
        self.seed_source(TENANT, OWNER, '510')
        self.record(source_id='510')
        self.query('UPDATE saas_crm_tasks SET completed=TRUE WHERE tenant_id=%s::uuid AND kommo_task_id=510', (TENANT,))
        assert delivery.expand_events() == 0
        assert self.query("SELECT state FROM saas_proactive_risk_alert_events WHERE tenant_id=%s::uuid AND source_id='510'", (TENANT,))[0]['state'] == 'cancelled'
        assert self.query('SELECT id FROM saas_tenant_notifications') == []

    def test_owner_change_and_tenant_isolation_cancel_delivery(self):
        self.record()
        self.query('UPDATE saas_tenants SET owner_telegram_id=999 WHERE id=%s::uuid', (TENANT,))
        assert delivery.expand_events() == 0
        assert self.query('SELECT id FROM saas_tenant_notifications') == []
        assert self.query('SELECT id FROM saas_proactive_risk_alert_events WHERE tenant_id=%s::uuid', (OTHER_TENANT,)) == []

    def test_revoked_owner_device_does_not_cancel_another_bound_device(self):
        self.record()
        second = 'c' * 56 + hashlib.sha256(b'second-device').hexdigest()[:8]
        self.query('INSERT INTO saas_push_devices(endpoint_hash,owner_telegram_id,subscription) VALUES(%s,%s,%s)',
                   (second, OWNER, 'second-encrypted-subscription'))
        self.query('''INSERT INTO saas_member_push_devices(tenant_id,telegram_id,endpoint_hash,active)
          VALUES(%s::uuid,%s,%s,TRUE)''', (TENANT, OWNER, second))
        assert delivery.expand_events() == 1
        revoked = self.endpoint_hash(TENANT, SOURCE_ID)
        self.query('''UPDATE saas_proactive_risk_alert_deliveries SET updated_at=now()-interval '1 day'
          WHERE tenant_id=%s::uuid AND channel='push' AND endpoint_hash=%s''', (TENANT, revoked))
        self.query('''UPDATE saas_member_push_devices SET active=FALSE
          WHERE tenant_id=%s::uuid AND endpoint_hash=%s''', (TENANT, revoked))
        item = delivery.claim_push()
        assert item and item['send'] is False and item['endpoint_hash'] == revoked
        assert self.query('SELECT state FROM saas_proactive_risk_alert_events WHERE tenant_id=%s::uuid',
                          (TENANT,))[0]['state'] == 'expanded'
        item = delivery.claim_push()
        assert item and item['send'] is True and item['endpoint_hash'] == second
        assert self.query('SELECT state FROM saas_proactive_risk_alert_events WHERE tenant_id=%s::uuid',
                          (TENANT,))[0]['state'] == 'expanded'

    def test_device_revoked_during_slow_source_recheck_is_not_reserved(self, monkeypatch):
        self.record()
        assert delivery.expand_events() == 1
        authorize = delivery._authorize_event

        def revoke_after_source_check(cur, event, *, channel=None):
            result = authorize(cur, event, channel=channel)
            cur.execute('''UPDATE saas_member_push_devices SET active=FALSE
              WHERE tenant_id=%s::uuid AND telegram_id=%s AND endpoint_hash=%s''',
              (TENANT, OWNER, self.endpoint_hash(TENANT, SOURCE_ID)))
            return result

        monkeypatch.setattr(delivery, '_authorize_event', revoke_after_source_check)
        item = delivery.claim_push()
        assert item and item['send'] is False
        assert self.query("SELECT status FROM saas_proactive_risk_alert_deliveries WHERE channel='push'")[0]['status'] == 'skipped'

    def test_daily_company_cap_suppresses_eleventh_distinct_source(self):
        for source_id in range(600, 611):
            self.seed_source(TENANT, OWNER, str(source_id))
            self.record(source_id=str(source_id))
        states = self.query('SELECT state FROM saas_proactive_risk_alert_events WHERE tenant_id=%s::uuid', (TENANT,))
        assert sum(row['state'] == 'pending' for row in states) == 10
        assert sum(row['state'] == 'suppressed' for row in states) == 1

    def test_provider_acceptance_is_not_reported_as_read_receipt(self):
        self.record()
        assert delivery.expand_events() == 1
        item = delivery.claim_push()
        assert item and item['send'] is True
        delivery.finish_push(item, 'accepted')
        status = self.query("SELECT status FROM saas_proactive_risk_alert_deliveries WHERE channel='push'")[0]['status']
        assert status == 'accepted'

    def assertBellAndPush(self):
        row = self.query('SELECT * FROM saas_tenant_notifications WHERE tenant_id=%s::uuid', (TENANT,))[0]
        assert row['telegram_id'] == OWNER
        assert row['title'] == 'Risk yoxlaması tələb olunur'
        assert 'Change price' not in row['body']
        alert_id=self.query("SELECT id FROM saas_proactive_risk_alert_events WHERE tenant_id=%s::uuid",(TENANT,))[0]['id']
        assert row['target_url'] == '/app?view=linear&risk_alert_id='+str(alert_id)
        deliveries = self.query('''SELECT recipient_id,channel,endpoint_hash,status
          FROM saas_proactive_risk_alert_deliveries ORDER BY channel''')
        assert [(x['recipient_id'], x['channel']) for x in deliveries] == [(OWNER,'internal'),(OWNER,'push')]

    def test_single_alert_detail_is_owner_scoped_and_fails_closed(self, monkeypatch):
        from tenant_linear_policy import TenantLinearError
        self.record()
        alert_id=str(self.query('SELECT id FROM saas_proactive_risk_alert_events')[0]['id'])
        self.query("UPDATE saas_proactive_risk_alert_events SET state='expanded'")
        monkeypatch.setattr(risk_store,'_connect',lambda:self.db)
        monkeypatch.setattr(risk_store,'ensure_schema',lambda _conn:None)
        finding={'source_kind':'crm_task','source_id':SOURCE_ID,'source':'CRM task','source_title':'CRM task 509',
                 'source_url':'/app?view=tasks','rule_id':'service_unavailable','severity':'P1',
                 'confidence':'high','facts':['Task is overdue'],'assessment':'Needs review',
                 'recommended_step':'Assign an owner'}
        monkeypatch.setattr(delivery,'current_finding',lambda *_args:finding)
        owner={'tenant_id':TENANT,'telegram_id':OWNER,'role':'owner','active':True,'tenant_status':'active'}
        detail=risk_store.alert_detail(owner,alert_id)
        assert detail['source_title']=='CRM task 509'
        assert detail['facts']==['Task is overdue']
        assert detail['source_url']=='/app?view=tasks'
        with pytest.raises(TenantLinearError) as denied:
            risk_store.alert_detail({**owner,'role':'admin'},alert_id)
        assert denied.value.status==403
        monkeypatch.setattr(delivery,'current_finding',lambda *_args:None)
        with pytest.raises(TenantLinearError) as stale:
            risk_store.alert_detail(owner,alert_id)
        assert stale.value.status==404


def test_preferences_make_risk_opt_in_owner_only_and_no_telegram():
    risk = next(row for row in tenant_notifications.notification_catalog() if row['key'] == 'proactive_risk')
    assert risk['roles'] == ['owner']
    assert risk['channels'] == ['internal', 'push']
    with pytest.raises(ValueError):
        tenant_notifications.validate_notification_preferences({'proactive_risk': {'telegram': True}})
    policy = TenantPolicy({'tenant_id': TENANT, 'telegram_id': OWNER, 'role': 'owner', 'active': True,
        'notification_rules': {}, 'workflow': {'policies': {'members': {
            str(OWNER): {'notifications': {'proactive_risk': {
                'internal': True, 'push': True, 'telegram': True}}}}}}})
    assert tenant_notifications.notification_channels(policy, event='proactive_risk', tenant_id=TENANT) == ['internal','push']
    admin = TenantPolicy({'tenant_id': TENANT, 'telegram_id': 303, 'role': 'admin', 'active': True,
        'permissions': ['settings'], 'notification_rules': {}, 'workflow': {'policies': {'members': {
            '303': {'notifications': {'proactive_risk': {'internal': True, 'push': True}}}}}}})
    assert 'proactive_risk' not in tenant_notifications.available_notification_events(admin)
    assert tenant_notifications.notification_channels(admin, event='proactive_risk', tenant_id=TENANT) == []


def test_alert_threshold_never_promotes_low_confidence_p2():
    assert delivery.alertable({'severity':'P1','confidence':'low'})
    assert delivery.alertable({'severity':'P2','confidence':'medium'})
    assert not delivery.alertable({'severity':'P2','confidence':'low'})


def test_push_payload_is_neutral_and_routes_to_bell_section():
    payload = payload_for('proactive_risk')
    assert payload == {
        'title': 'CRM Smart Assistant',
        'body': 'Tapşırıq üzrə yoxlanmalı siqnal var. Kabinetdə yoxlayın.',
        'url': '/app?view=linear', 'tag': 'crm-proactive-risk', 'kind': 'risk_alert'}
    assert not any(value in str(payload) for value in (TENANT, SOURCE_ID, 'price', 'permission'))
    alert_id='01a1b1af-4d2c-71f2-aaeb-f01eccac0031'
    assert payload_for('proactive_risk','/app?view=linear&risk_alert_id='+alert_id)['url'] == '/app?view=linear&risk_alert_id='+alert_id


def test_linear_delivery_rechecks_exact_team_and_live_issue(monkeypatch):
    from tenant_proactive_risk_policy import linear_scope
    import tenant_linear_context
    import tenant_linear_tasks_provider
    import tenant_proactive_risk_policy

    version = '2026-11-07T12:00:00+00:00'
    scope = linear_scope(version, 'team-a')
    finding = {'rule_id':'access_control_claim','severity':'P2','confidence':'medium'}
    event = {'tenant_id':TENANT,'source_kind':'linear','source_id':'issue-1','rule_id':finding['rule_id'],
             'source_scope':scope,'alert_fingerprint':delivery.alert_fingerprint(finding)}
    stored = {**finding,'active':True,'source_scope':scope}
    cur = Mock(); cur.fetchone.return_value = stored
    config = {'team_id':'team-a','done_state_ids':[],'workflow':{'enabled':True,'sync_enabled':True}}
    issue = {'id':'issue-1','team':{'id':'team-a'},'state':{'id':'todo'},
             'updatedAt':version,'title':'Access problem','description':'permission change',
             'url':'https://linear.app/acme/issue/issue-1'}
    monkeypatch.setattr(tenant_linear_context,'context',lambda _cur,_session: ({},config,'api-key',version))
    monkeypatch.setattr(tenant_linear_tasks_provider,'issue',lambda _key,_id: issue)
    monkeypatch.setattr(tenant_proactive_risk_policy,'analyze_linear',lambda *_args,**_kwargs:[finding])
    owner = {'tenant_id':TENANT,'telegram_id':OWNER,'onboarding':{}}
    assert delivery._finding_is_current(cur,event,owner) is True
    detail=delivery.current_finding(cur,event,owner)
    assert detail['source_title']=='Access problem'
    assert detail['source_url']=='https://linear.app/acme/issue/issue-1'
    assert detail['rule_id']=='access_control_claim'
    issue['team']={'id':'other-team'}
    assert delivery._finding_is_current(cur,event,owner) is False
    assert delivery.current_finding(cur,event,owner) is None
