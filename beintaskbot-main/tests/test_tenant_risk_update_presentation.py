"""Read-only risk update projections never expose cached source text."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from test_tenant_hot_orders import load

TENANT = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
EVENT = 'bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb'


class UpdatePresentation(unittest.TestCase):
    def setUp(self):
        self.cur = MagicMock()
        self.owner = {'tenant_id': TENANT, 'telegram_id': 20, 'role': 'owner',
                      'active': True, 'tenant_status': 'active'}
        self.event = {'id': EVENT, 'tenant_id': TENANT, 'recipient_id': 20,
                      'event_type': 'linear_update', 'state': 'expanded'}
        self.detail = {'source_kind': 'linear', 'source_id': 'issue',
                       'source_title': 'Current issue', 'source_url': 'https://linear.app/team/issue/1',
                       'context': {'status': 'Completed', 'assigned': True}}
        self.delivery = SimpleNamespace(_owner_profile=Mock(return_value=self.owner),
            _channels=Mock(return_value=['internal']), current_finding=Mock(),
            material_update_detail=Mock(return_value=self.detail),
            material_update_details=Mock(return_value={EVENT: self.detail}),
            material_update_text=Mock(return_value=('Task updated', 'Linear status: Completed.')),
            material_update_payload=Mock(return_value={'state_changed': True, 'new_state': 'Completed'}))
        self.center = load('tenant_notification_center', {'_connect': Mock(), '_read_tenant_workflow': Mock()}, {'tenant_platform'})
        self.row = {'event_key': 'proactive_risk:' + EVENT, 'title': 'Neutral',
                    'body': 'No source data', 'target_url': '/app?view=linear&risk_alert_id=' + EVENT}

    def hydrate(self):
        self.cur.fetchone.side_effect = [{'ready': True}]
        self.cur.fetchall.return_value = [self.event] if self.event else []
        with patch.dict(sys.modules, {'tenant_proactive_risk_delivery': self.delivery}):
            self.center._hydrate_risk_updates(self.cur, TENANT, 20, [self.row])

    def test_current_live_copy_and_personal_scope(self):
        self.hydrate()
        self.assertEqual(self.row['body'], 'Linear status: Completed.')
        self.assertEqual(self.cur.execute.call_args.args[1], (TENANT, 20, [EVENT]))
        self.assertIn('recipient_id=%s', self.cur.execute.call_args.args[0])
        self.assertIn('tenant_id=%s::uuid', self.cur.execute.call_args.args[0])
        self.delivery.material_update_details.assert_called_once_with(self.cur, [self.event], self.owner, timeout=(1, 2))
        self.assertIn(EVENT, self.row['target_url'])

    def test_source_timeout_and_revoked_owner_fail_closed(self):
        for failure in ('timeout', 'owner', 'opt_out', 'missing_source', 'cancelled'):
            with self.subTest(failure=failure):
                self.setUp()
                if failure == 'timeout': self.delivery.material_update_details.side_effect = TimeoutError()
                if failure == 'owner': self.delivery._owner_profile.return_value = None
                if failure == 'opt_out': self.delivery._channels.return_value = ['push']
                if failure == 'missing_source': self.delivery.material_update_details.return_value = {}
                if failure == 'cancelled': self.event['state'] = 'cancelled'
                self.hydrate()
                self.assertEqual(self.row['target_url'], '')
                self.assertEqual(self.row['target_state'], 'unavailable')
                self.assertNotIn('Completed', self.row['body'])

    def test_unknown_or_other_tenant_event_has_no_target_or_source_copy(self):
        self.event = None
        self.hydrate()
        self.assertEqual(self.row['target_url'], '')
        self.delivery.material_update_details.assert_not_called()
        self.assertEqual(self.cur.execute.call_args.args[1], (TENANT, 20, [EVENT]))

    def test_public_payload_only_after_authorized_hydration(self):
        self.hydrate()
        public = self.center._public(self.row)
        self.assertEqual(public['risk_update']['new_state'], 'Completed')
        self.setUp()
        self.delivery._owner_profile.return_value = None
        self.hydrate()
        self.assertNotIn('risk_update', self.center._public(self.row))

    def test_initial_risks_unchanged(self):
        self.event['event_type'] = 'finding'
        before = self.row.copy()
        self.hydrate()
        self.assertEqual(self.row, before)
        self.delivery.material_update_details.assert_not_called()

    def test_update_detail_can_show_completed_without_active_finding(self):
        self.delivery._channels.return_value = ['push']
        connect = MagicMock()
        conn = connect.return_value.__enter__.return_value
        conn.cursor.return_value.__enter__.return_value = self.cur
        self.cur.fetchone.return_value = self.event
        store = load('tenant_proactive_risk_store', {'_connect': connect,
            '_read_tenant_workflow': Mock(), 'ensure_schema': Mock()},
            {'tenant_platform', 'tenant_proactive_risk_schema'})
        with patch.dict(sys.modules, {'tenant_proactive_risk_delivery': self.delivery}):
            detail = store.alert_detail(self.owner, EVENT)
        self.assertEqual(detail['context']['status'], 'Completed')
        self.assertEqual(detail['event_type'], 'linear_update')
        self.assertEqual(detail['update_body'], 'Linear status: Completed.')
        self.delivery.current_finding.assert_not_called()
        self.assertEqual(detail['update_data']['new_state'], 'Completed')
        self.assertEqual(self.cur.execute.call_args.args[1], (TENANT, 20, 'proactive_risk:' + EVENT))


if __name__ == '__main__':
    unittest.main()


def test_twenty_five_updates_use_one_batch_each_poll():
    from uuid import uuid4
    test=UpdatePresentation(); test.setUp()
    events=[{**test.event,'id':str(uuid4())} for _ in range(25)]
    rows=[{'event_key':'proactive_risk:'+e['id'],'title':'Neutral','body':'Neutral','target_url':''} for e in events]
    test.delivery.material_update_details.return_value={e['id']:test.detail for e in events}
    test.cur.fetchone.side_effect=[{'ready':True},{'ready':True}]
    test.cur.fetchall.return_value=events
    with patch.dict(sys.modules, {'tenant_proactive_risk_delivery':test.delivery}):
        test.center._hydrate_risk_updates(test.cur,TENANT,20,rows)
        test.center._hydrate_risk_updates(test.cur,TENANT,20,rows)
    assert test.delivery.material_update_details.call_count==2
    assert all(row.get('risk_update',{}).get('new_state')=='Completed' for row in rows)
    assert all(row['target_url'] for row in rows)
