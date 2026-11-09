"""Observer/expansion interleavings must preserve the undelivered change origin."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

import test_tenant_risk_update_coalescing as coalescing
from test_tenant_proactive_risk_concurrency_postgres import risk_postgres, _profiled_connection
from tenant_proactive_risk_policy import linear_scope


delivery = coalescing.delivery
TENANT, OWNER = coalescing.TENANT, coalescing.OWNER
Base = coalescing.TestRiskUpdateCoalescing


class TestRiskUpdateInterleavings:
    setup_database = Base.setup_database
    read_workflow = Base.read_workflow
    create_tables = Base.create_tables
    seed_company = Base.seed_company
    task = Base.task
    seed_source = Base.seed_source
    endpoint_hash = staticmethod(Base.endpoint_hash)
    query = Base.query
    ISSUE, TEAM, OLD, NEW = Base.ISSUE, Base.TEAM, Base.OLD, Base.NEW
    linear_issue = Base.linear_issue
    delivered_episode = Base.delivered_episode
    updated = Base.updated
    queue = Base.queue
    issue_at = Base.issue_at
    live = Base.live
    resolve_original = Base.resolve_original
    active_updates = Base.active_updates

    @pytest.mark.parametrize('expansion_first', [True, False], ids=['expand-before-observe', 'observe-before-expand'])
    def test_resolved_risk_keeps_earliest_owner_across_expansion_order(self, monkeypatch, expansion_first):
        original_id = self.delivered_episode()
        original = self.linear_issue(owner='a')
        middle = self.issue_at(1, owner='b')
        latest = self.issue_at(2, owner='c')
        self.queue(original, middle, done_state_ids=['done'])
        stale_id = str(self.active_updates()[0]['id'])
        self.resolve_original(middle)
        assert self.query('SELECT state FROM saas_proactive_risk_alert_events WHERE id=%s::uuid',
                          (original_id,))[0]['state'] == 'cancelled'
        self.live(monkeypatch, latest)
        if expansion_first:
            # Linear is ahead of the observer. Expansion must not send A→B.
            assert delivery.expand_events() == 0
            assert self.query('SELECT id FROM saas_tenant_notifications') == []
        self.queue(middle, latest, done_state_ids=['done'])
        # Replayed observation must not create another event or lose A.
        self.queue(middle, latest, done_state_ids=['done'])
        active = self.active_updates()
        assert len(active) == 1, 'source validation cannot erase the resolved risk episode anchor'
        final = active[0]
        assert str(final['id']) != stale_id
        assert final['update_data']['old_owner'] == 'Ali'
        assert final['update_data']['old_owner_id'] == 'a'
        assert final['update_data']['owner_id'] == 'c'
        assert delivery.expand_events() == 1
        assert delivery.expand_events() == 0
        assert len(self.query('SELECT id FROM saas_tenant_notifications')) == 1
        sent = self.query("""SELECT e.id FROM saas_proactive_risk_alert_events e
          JOIN saas_proactive_risk_alert_deliveries d ON d.tenant_id=e.tenant_id AND d.event_id=e.id
          WHERE e.event_type='linear_update' AND d.channel='internal' AND d.status='delivered'""")
        assert [str(row['id']) for row in sent] == [str(final['id'])]

    def test_opt_out_cancellation_then_completion_still_closes_episode(self, monkeypatch):
        self.delivered_episode()
        original = self.linear_issue(owner='a')
        assigned = self.issue_at(1, owner='b')
        self.queue(original, assigned, done_state_ids=['done'])
        self.resolve_original(assigned)
        self.preferences[OWNER] = {}
        self.live(monkeypatch, assigned)
        assert delivery.expand_events() == 0
        completed = self.issue_at(2, owner='b', state='done')
        self.queue(assigned, completed, done_state_ids=['done'])
        markers = [row for row in self.updated() if row['update_data'].get('episode_closed')]
        assert markers, 'a genuine opt-out must not prevent persisting episode closure'
        assert all(row['quota_reserved_at'] is None for row in markers)
        self.preferences[OWNER] = {'internal': True, 'push': True}
        reopened = self.issue_at(3, owner='b')
        changed = self.issue_at(4, owner='c')
        self.queue(completed, reopened, done_state_ids=['done'])
        self.queue(reopened, changed, done_state_ids=['done'])
        assert self.active_updates() == []
        assert delivery.expand_events() == 0
        assert self.query('SELECT id FROM saas_tenant_notifications') == []

    @pytest.mark.parametrize('internal', [True, False], ids=['bell-delivered', 'push-only-unsent'])
    def test_push_revalidation_before_observer_preserves_episode(self, monkeypatch, internal):
        self.delivered_episode()
        original=self.linear_issue(owner='a')
        middle=self.issue_at(1,owner='b')
        latest=self.issue_at(2,owner='c')
        self.preferences[OWNER]={'internal':internal,'push':True}
        self.queue(original,middle,done_state_ids=['done'])
        self.resolve_original(middle)
        self.live(monkeypatch,middle)
        assert delivery.expand_events()==1
        self.live(monkeypatch,latest)
        item=delivery.claim_push()
        assert item and not item['send']
        self.queue(middle,latest,done_state_ids=['done'])
        active=self.active_updates(); assert len(active)==1
        data=active[0]['update_data']
        assert data['owner_id']=='c'
        # A delivered bell is already visible, but an unsent push must carry
        # the earlier origin forward into the next update.
        assert data['old_owner_id']==('b' if internal else 'a')


    @pytest.mark.parametrize('cancel_reason', ['stale', 'opt_out'])
    @pytest.mark.parametrize('aged_prior', [False, True], ids=['recent-prior', 'ninety-one-day-prior'])
    def test_new_episode_never_inherits_cancelled_old_assignment(self, monkeypatch, cancel_reason, aged_prior):
        self.delivered_episode()
        original = self.linear_issue(owner='a')
        assigned = self.issue_at(1, owner='b')
        self.queue(original, assigned, done_state_ids=['done'])
        self.resolve_original(assigned)
        if cancel_reason == 'opt_out':
            self.preferences[OWNER] = {}
            self.live(monkeypatch, assigned)
        else:
            self.live(monkeypatch, self.issue_at(2, owner='c'))
        assert delivery.expand_events() == 0
        self.preferences[OWNER] = {}
        completed = self.issue_at(3, owner='b', state='done')
        self.queue(assigned, completed, done_state_ids=['done'])
        assert any(row['update_data'].get('episode_closed') for row in self.updated())
        # Explicit ordering makes a later delivered risk a distinct episode.
        interval = '91 days' if aged_prior else '1 minute'
        self.query("UPDATE saas_proactive_risk_alert_events SET created_at=now()-%s::interval", (interval,))
        previous_ids = {str(row['id']) for row in self.updated()}
        reopened = self.issue_at(4, owner='c')
        self.queue(completed, reopened, done_state_ids=['done'])
        self.preferences[OWNER] = {'internal': True, 'push': True}
        self.delivered_episode()
        changed = self.issue_at(5, owner='d')
        self.queue(reopened, changed, done_state_ids=['done'])
        active = self.active_updates()
        assert len(active) == 1 and str(active[0]['id']) not in previous_ids
        payload = active[0]['update_data']
        assert payload['old_owner_id'] == 'c', 'closed or expired A→B must not contaminate new C→D'
        assert payload['old_owner'] == 'c' and payload['owner_id'] == 'd'
        self.live(monkeypatch, changed)
        assert delivery.expand_events() == 1
        assert len(self.query('SELECT id FROM saas_tenant_notifications')) == 1


def test_native_expansion_and_observer_race_preserves_one_update(risk_postgres, monkeypatch):
    """Real row/advisory locks: simultaneous workers finish without deadlock."""
    dsn, schema, tenant = risk_postgres
    issue_id, team_id, original_id = str(uuid4()), str(uuid4()), str(uuid4())
    version = coalescing.fixtures.NOW
    scope = linear_scope(version, team_id)

    def connect():
        conn = _profiled_connection(dsn, schema)
        conn.execute("SET statement_timeout='5s'")
        conn.execute("SET lock_timeout='5s'")
        return conn

    def issue(owner, minute):
        stamp = (version + coalescing.timedelta(minutes=minute)).isoformat()
        return {'id': issue_id, 'identifier': 'RACE-1', 'title': 'Risk task', 'updatedAt': stamp,
                'team': {'id': team_id}, 'state': {'id': 'todo', 'name': 'Todo'},
                'assignee': {'id': owner, 'name': owner.upper()}, 'url': 'https://linear.app/team/issue/RACE-1'}

    original, middle, latest = issue('a', 0), issue('b', 1), issue('c', 2)
    with connect() as conn:
        conn.execute('''CREATE TABLE saas_tenant_notifications(
          id UUID PRIMARY KEY,tenant_id UUID NOT NULL,telegram_id BIGINT NOT NULL,event_key TEXT NOT NULL,
          title TEXT NOT NULL,body TEXT NOT NULL,target_url TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),read_at TIMESTAMPTZ)''')
        conn.execute('''INSERT INTO saas_proactive_risk_alert_events
          (id,tenant_id,recipient_id,source_kind,source_id,rule_id,source_scope,source_version,alert_fingerprint,state)
          VALUES(%s,%s,101,'linear',%s,'risk',%s,%s,'risk','expanded')''',
          (original_id, tenant, issue_id, scope, original['updatedAt']))
        conn.execute('''INSERT INTO saas_proactive_risk_alert_deliveries
          (tenant_id,event_id,recipient_id,channel,status) VALUES(%s,%s,101,'internal','delivered')''',
          (tenant, original_id))
        with conn.cursor() as cur:
            delivery.record_linear_material_update(cur, tenant, scope, middle['updatedAt'], original, middle, done_state_ids=['done'])
            delivery.record_finding_transitions(cur, tenant, 'linear', issue_id, scope, middle['updatedAt'], [])
    monkeypatch.setattr(delivery, '_connect', connect)
    monkeypatch.setattr(delivery, 'ensure_schema', lambda _conn: None)
    monkeypatch.setattr(delivery, 'seed_baseline_alerts', lambda: 0)
    monkeypatch.setattr(delivery, 'promote_deferred_events', lambda: 0)
    config = {'team_id': team_id, 'done_state_ids': ['done'], 'workflow': {'enabled': True, 'sync_enabled': True}}
    monkeypatch.setattr('tenant_linear_context.context', lambda *a, **k: ({}, config, 'key', version))
    monkeypatch.setattr('tenant_linear_tasks_provider.issue', lambda *a, **k: latest)
    gate = Barrier(2)

    def observe():
        gate.wait(timeout=10)
        with connect() as conn:
            with conn.cursor() as cur:
                delivery.record_linear_material_update(cur, tenant, scope, latest['updatedAt'], middle, latest, done_state_ids=['done'])

    def expand():
        gate.wait(timeout=10)
        return delivery.expand_events()

    with ThreadPoolExecutor(max_workers=2) as workers:
        observing = workers.submit(observe)
        expanding = workers.submit(expand)
        observing.result(timeout=15)
        expanding.result(timeout=15)
    delivery.expand_events()
    with connect() as conn:
        rows = conn.execute("""SELECT e.* FROM saas_proactive_risk_alert_events e
          JOIN saas_proactive_risk_alert_deliveries d ON d.tenant_id=e.tenant_id AND d.event_id=e.id
          WHERE e.tenant_id=%s AND e.event_type='linear_update'
            AND d.channel='internal' AND d.status='delivered'""", (tenant,)).fetchall()
        assert len(rows) == 1
        assert rows[0]['update_data']['old_owner_id'] == 'a'
        assert rows[0]['update_data']['owner_id'] == 'c'
        assert conn.execute('SELECT count(*) AS count FROM saas_tenant_notifications').fetchone()['count'] == 1


def test_native_two_device_stale_source_push_claims_do_not_send_or_deadlock(risk_postgres, monkeypatch):
    """Two pending devices for an expanded update become stale before claim."""
    import json
    dsn, schema, tenant = risk_postgres
    event_id, issue_id, team_id = str(uuid4()), str(uuid4()), str(uuid4())
    version = coalescing.fixtures.NOW
    scope = linear_scope(version, team_id)

    def connect():
        conn = _profiled_connection(dsn, schema)
        conn.execute("SET statement_timeout='5s'")
        conn.execute("SET lock_timeout='5s'")
        return conn

    current = {'issue': {'id': issue_id, 'identifier': 'PUSH-1', 'title': 'Risk task',
        'updatedAt': version.isoformat(), 'team': {'id': team_id},
        'state': {'id': 'todo', 'name': 'Todo'}, 'assignee': {'id': 'b', 'name': 'B'},
        'url': 'https://linear.app/team/issue/PUSH-1'}}
    with connect() as conn:
        conn.execute("INSERT INTO saas_push_devices VALUES('device-second',101,%s)", (b'encrypted-second',))
        conn.execute("INSERT INTO saas_member_push_devices VALUES(%s,101,'device-second',TRUE)", (tenant,))
        data = {'owner_changed': True, 'old_owner_id': 'a', 'old_owner': 'A',
                'old_owner_assigned': True, 'owner_id': 'b', 'state_id': 'todo', 'state_changed': False}
        conn.execute("""INSERT INTO saas_proactive_risk_alert_events
          (id,tenant_id,recipient_id,source_kind,source_id,rule_id,source_scope,source_version,
           alert_fingerprint,state,event_type,update_data,quota_reserved_at)
          VALUES(%s,%s,101,'linear',%s,'linear_update',%s,%s,'update','pending','linear_update',%s::jsonb,now())""",
          (event_id, tenant, issue_id, scope, version.isoformat(), json.dumps(data)))
    monkeypatch.setattr(delivery, '_connect', connect)
    monkeypatch.setattr(delivery, 'ensure_schema', lambda _conn: None)
    monkeypatch.setattr(delivery, 'seed_baseline_alerts', lambda: 0)
    monkeypatch.setattr(delivery, 'promote_deferred_events', lambda: 0)
    monkeypatch.setattr(delivery, '_channels', lambda _profile, _tenant: ['push'])
    config = {'team_id': team_id, 'done_state_ids': ['done'], 'workflow': {'enabled': True, 'sync_enabled': True}}
    monkeypatch.setattr('tenant_linear_context.context', lambda *a, **k: ({}, config, 'key', version))
    monkeypatch.setattr('tenant_linear_tasks_provider.issue', lambda *a, **k: current['issue'])
    assert delivery.expand_events() == 1
    with connect() as conn:
        assert conn.execute("SELECT state FROM saas_proactive_risk_alert_events WHERE id=%s", (event_id,)).fetchone()['state'] == 'expanded'
        pending = conn.execute("SELECT status FROM saas_proactive_risk_alert_deliveries WHERE event_id=%s", (event_id,)).fetchall()
        assert len(pending) == 2 and all(row['status'] == 'pending' for row in pending)
    current['issue'] = {**current['issue'], 'assignee': {'id': 'c', 'name': 'C'},
                        'updatedAt': (version + coalescing.timedelta(minutes=1)).isoformat()}
    gate = Barrier(2)

    def claim():
        gate.wait(timeout=10)
        return delivery.claim_push()

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(claim) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert all(item is None or item['send'] is False for item in results)
    assert delivery.claim_push() is None
    with connect() as conn:
        rows = conn.execute("""SELECT endpoint_hash,status FROM saas_proactive_risk_alert_deliveries
          WHERE event_id=%s AND channel='push'""", (event_id,)).fetchall()
        assert {row['endpoint_hash'] for row in rows} == {'device-hash', 'device-second'}
        assert len(rows) == 2 and all(row['status'] == 'skipped' for row in rows)
        assert not any(row['status'] in ('sending', 'accepted', 'unknown') for row in rows)
