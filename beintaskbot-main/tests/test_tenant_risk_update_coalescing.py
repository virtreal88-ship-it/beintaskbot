"""Isolated PostgreSQL regressions for one coalesced, delivered risk episode."""
from datetime import datetime, timedelta

import test_tenant_proactive_risk_delivery as fixtures


delivery = fixtures.delivery
TENANT, OWNER = fixtures.TENANT, fixtures.OWNER


class TestRiskUpdateCoalescing:
    # Reuse the real PGlite schema/setup, not the base class's collected tests.
    setup_database = fixtures.TestRiskAlertPostgres.setup_database
    read_workflow = fixtures.TestRiskAlertPostgres.read_workflow
    create_tables = fixtures.TestRiskAlertPostgres.create_tables
    seed_company = fixtures.TestRiskAlertPostgres.seed_company
    task = fixtures.TestRiskAlertPostgres.task
    seed_source = fixtures.TestRiskAlertPostgres.seed_source
    endpoint_hash = staticmethod(fixtures.TestRiskAlertPostgres.endpoint_hash)
    query = fixtures.TestRiskAlertPostgres.query
    ISSUE = fixtures.TestLinearMaterialUpdates.ISSUE
    TEAM = fixtures.TestLinearMaterialUpdates.TEAM
    OLD = fixtures.TestLinearMaterialUpdates.OLD
    NEW = fixtures.TestLinearMaterialUpdates.NEW
    linear_issue = fixtures.TestLinearMaterialUpdates.linear_issue
    delivered_episode = fixtures.TestLinearMaterialUpdates.delivered_episode
    updated = fixtures.TestLinearMaterialUpdates.updated

    def queue(self, before, after, *, done_state_ids=None):
        kwargs = {} if done_state_ids is None else {'done_state_ids': done_state_ids}
        with self.db.cursor() as cur:
            delivery.record_linear_material_update(
                cur, TENANT, self.scope, after['updatedAt'], before, after, **kwargs)

    def issue_at(self, minute, **kwargs):
        return self.linear_issue(stamp=(datetime.fromisoformat(self.OLD)
                                       + timedelta(minutes=minute)).isoformat(), **kwargs)

    def live(self, monkeypatch, issue):
        config = {'team_id': self.TEAM, 'done_state_ids': ['done'],
                  'workflow': {'enabled': True, 'sync_enabled': True}}
        monkeypatch.setattr('tenant_linear_context.context',
                            lambda *a, **k: ({}, config, 'key', self.version))
        monkeypatch.setattr('tenant_linear_tasks_provider.issue', lambda *a, **k: issue)

    def resolve_original(self, issue):
        with self.db.cursor() as cur:
            delivery.record_finding_transitions(cur, TENANT, 'linear', self.ISSUE,
                                               self.scope, issue['updatedAt'], [])

    def active_updates(self):
        return [row for row in self.updated() if row['state'] in ('pending', 'deferred')]

    def test_more_than_daily_limit_changes_release_unsent_reservations(self):
        self.delivered_episode()
        before = self.linear_issue()
        # Unique owners avoid accidentally returning to the starting owner.
        for minute in range(1, delivery.MAX_RISK_ALERTS_PER_DAY + 4):
            after = self.issue_at(minute, owner='owner-' + str(minute))
            self.queue(before, after)
            before = after
        active = self.active_updates()
        assert len(active) == 1
        assert active[0]['state'] == 'pending', 'superseded unsent updates must not exhaust quota'
        assert active[0]['quota_reserved_at'] is not None
        assert active[0]['update_data']['old_owner'] == 'Ali'
        assert active[0]['update_data']['owner_id'] == before['assignee']['id']
        assert all(row['quota_reserved_at'] is None for row in self.updated()
                   if row['state'] == 'cancelled'), 'only the final unsent change may reserve quota'
        assert self.query("""SELECT count(*) AS count FROM saas_proactive_risk_alert_events
          WHERE tenant_id=%s::uuid AND event_type='linear_update'
            AND quota_reserved_at IS NOT NULL""", (TENANT,))[0]['count'] == 1

    def test_owner_then_completion_coalesces_earliest_and_latest_fields(self, monkeypatch):
        self.delivered_episode()
        original = self.linear_issue(owner='a', state='todo')
        assigned = self.issue_at(1, owner='b', state='todo')
        completed = self.issue_at(2, owner='b', state='done')
        self.queue(original, assigned, done_state_ids=['done'])
        self.queue(assigned, completed, done_state_ids=['done'])
        active = self.active_updates()
        assert len(active) == 1
        data = active[0]['update_data']
        assert data['owner_changed'] and data['state_changed']
        assert data['old_owner'] == 'Ali' and data['owner_id'] == 'b'
        assert data['old_state'] == 'Todo' and data['state_id'] == 'done'
        self.live(monkeypatch, completed)
        assert delivery.expand_events() == 1
        assert len(self.query('SELECT id FROM saas_tenant_notifications')) == 1
        with self.db.cursor() as cur:
            detail = delivery.material_update_detail(cur, active[0], delivery._owner_profile(cur, TENANT, OWNER))
        payload = delivery.material_update_payload(active[0], detail)
        assert payload['old_owner'] == 'Ali' and payload['new_owner'] == 'Sara'
        assert payload['old_state'] == 'Todo' and payload['new_state'] == 'Done'

    def test_owner_rollback_before_delivery_has_no_notification_or_quota(self, monkeypatch):
        self.delivered_episode()
        original = self.linear_issue(owner='a')
        assigned = self.issue_at(1, owner='b')
        reverted = self.issue_at(2, owner='a')
        self.queue(original, assigned)
        self.queue(assigned, reverted)
        assert self.active_updates() == [], 'A → B → A is not a material undelivered change'
        assert all(row['quota_reserved_at'] is None for row in self.updated())
        self.live(monkeypatch, reverted)
        assert delivery.expand_events() == 0
        assert self.query('SELECT id FROM saas_tenant_notifications') == []
        assert self.query("""SELECT d.event_id FROM saas_proactive_risk_alert_deliveries d
          JOIN saas_proactive_risk_alert_events e ON e.tenant_id=d.tenant_id AND e.id=d.event_id
          WHERE e.event_type='linear_update'""") == []

    def test_delivered_assignment_continues_episode_after_original_risk_resolves(self, monkeypatch):
        original_event = self.delivered_episode()
        original = self.linear_issue(owner='', state='todo')
        assigned = self.issue_at(1, owner='b', state='todo')
        self.queue(original, assigned, done_state_ids=['done'])
        self.resolve_original(assigned)
        assert self.query('SELECT state FROM saas_proactive_risk_alert_events WHERE id=%s::uuid',
                          (original_event,))[0]['state'] == 'cancelled'
        self.live(monkeypatch, assigned)
        assert delivery.expand_events() == 1
        first = next(row for row in self.updated() if row['state'] == 'expanded')
        completed = self.issue_at(2, owner='b', state='done')
        self.queue(assigned, completed, done_state_ids=['done'])
        active = self.active_updates()
        assert len(active) == 1 and str(active[0]['id']) != str(first['id'])
        assert active[0]['update_data']['state_changed']
        assert not active[0]['update_data']['owner_changed']
        self.live(monkeypatch, completed)
        assert delivery.expand_events() == 1
        assert len(self.query('SELECT id FROM saas_tenant_notifications')) == 2

    def test_reopening_after_done_does_not_continue_closed_episode(self, monkeypatch):
        self.delivered_episode()
        original = self.linear_issue(owner='a', state='todo')
        completed = self.issue_at(1, owner='a', state='done')
        self.queue(original, completed, done_state_ids=['done'])
        self.resolve_original(completed)
        self.live(monkeypatch, completed)
        assert delivery.expand_events() == 1
        delivered_ids = {str(row['id']) for row in self.updated()}
        reopened = self.issue_at(2, owner='b', state='todo')
        self.queue(completed, reopened, done_state_ids=['done'])
        # A subsequent open-task change also cannot revive the closed episode.
        reassigned = self.issue_at(3, owner='a', state='todo')
        self.queue(reopened, reassigned, done_state_ids=['done'])
        assert self.active_updates() == []
        assert {str(row['id']) for row in self.updated()} == delivered_ids
        self.live(monkeypatch, reassigned)
        assert delivery.expand_events() == 0
        assert len(self.query('SELECT id FROM saas_tenant_notifications')) == 1

    def test_new_risk_episode_after_reopening_is_not_blocked_by_old_closure(self, monkeypatch):
        self.delivered_episode()
        old=self.linear_issue(owner='a',state='todo')
        done=self.issue_at(1,owner='a',state='done')
        self.queue(old,done,done_state_ids=['done']); self.resolve_original(done)
        self.live(monkeypatch,done); assert delivery.expand_events()==1
        # Make the ordering explicit, as it would be after a later new scan.
        self.query("UPDATE saas_proactive_risk_alert_events SET created_at=now()-interval '1 minute'")
        self.delivered_episode()
        reopened=self.issue_at(2,owner='a',state='todo')
        changed=self.issue_at(3,owner='b',state='todo')
        self.queue(reopened,changed,done_state_ids=['done'])
        assert len(self.active_updates())==1

    def test_episode_tracking_expires_after_ninety_days(self):
        self.delivered_episode()
        self.query("UPDATE saas_proactive_risk_alert_events SET created_at=now()-interval '91 days'")
        self.queue(self.linear_issue(),self.issue_at(1,owner='b'),done_state_ids=['done'])
        assert self.updated()==[]

    def test_opted_out_completion_closes_episode_without_delivery(self, monkeypatch):
        original_event=self.delivered_episode()
        original=self.linear_issue(owner='',state='todo')
        assigned=self.issue_at(1,owner='b',state='todo')
        self.queue(original,assigned,done_state_ids=['done']); self.resolve_original(assigned)
        self.live(monkeypatch,assigned); assert delivery.expand_events()==1
        self.preferences[OWNER]={}
        done=self.issue_at(2,owner='b',state='done')
        self.queue(assigned,done,done_state_ids=['done'])
        markers=[r for r in self.updated() if r['state']=='suppressed']
        assert len(markers)==1 and markers[0]['update_data']=={'episode_closed':True}
        assert markers[0]['quota_reserved_at'] is None
        assert len(self.query('SELECT id FROM saas_tenant_notifications'))==1
        self.preferences[OWNER]={'internal':True,'push':True}
        reopened=self.issue_at(3,owner='b',state='todo')
        changed=self.issue_at(4,owner='a',state='todo')
        self.queue(done,reopened,done_state_ids=['done'])
        self.queue(reopened,changed,done_state_ids=['done'])
        assert self.active_updates()==[]
        assert delivery.expand_events()==0
        assert len(self.query('SELECT id FROM saas_tenant_notifications'))==1

    def test_schema_reinitialization_does_not_restore_released_quota(self, monkeypatch):
        from tenant_proactive_risk_schema import ensure_schema
        import tenant_proactive_risk_schema as schema
        self.delivered_episode()
        before=self.linear_issue()
        first=self.issue_at(1,owner='b')
        latest=self.issue_at(2,owner='c')
        self.queue(before,first); self.queue(first,latest)
        assert sum(r['quota_reserved_at'] is not None for r in self.updated())==1
        monkeypatch.setattr(schema,'_ready',False)
        monkeypatch.setattr(schema,'_ensure_schema',lambda _conn:None)
        ensure_schema(self.db)
        assert sum(r['quota_reserved_at'] is not None for r in self.updated())==1
        assert all(r['quota_reserved_at'] is None for r in self.updated() if r['state']=='cancelled')
