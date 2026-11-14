import unittest
from unittest.mock import Mock,patch
from datetime import datetime,timezone
from dataclasses import replace
from tenant_crm_import_provenance import observe_page,ObservedKommoPage
from tenant_crm_relation_contract import ProviderBinding
from tenant_task_flow import TaskFlowError
import tenant_kommo_board_provenance as proof

T='11111111-1111-4111-8111-111111111111'
B=ProviderBinding(T,'kommo','test.kommo.com','kommo_manual:2026-01-01T00:00:00+00:00')
class Proof(unittest.TestCase):
    def test_actual_task_observation_not_provider_json(self):
        credentials={'provider':'kommo_manual','account_domain':'test.kommo.com','connected_at':datetime(2026,1,1,tzinfo=timezone.utc)}
        page={'_embedded':{'tasks':[{'id':12}]},'observation':'forged'}
        result=observe_page(T,'tasks',credentials,page)
        self.assertIsInstance(result,ObservedKommoPage)
        self.assertEqual(result.observation.binding,B)
        self.assertEqual(result.observation.external_ids,frozenset({'12'}))
        self.assertEqual(result['observation'],'forged') # JSON never becomes evidence.
    @patch.object(proof,'ready',return_value=True)
    def test_cache_digest_and_binding_are_both_required(self,ready):
        cur=Mock();raw={'id':12,'complete_till':100}
        cur.fetchall.return_value=[{'external_id':12,'raw_digest':proof.digest(raw),'source_revision':'a'*64,'raw':raw}]
        self.assertEqual(proof.revisions(cur,T,'tasks',[12],B),{'12':'a'*64})
        self.assertIn(B.connection_generation,cur.execute.call_args.args[1])
        cur.fetchall.return_value=[{'external_id':12,'raw_digest':proof.digest({'id':12,'complete_till':90}),'source_revision':'a'*64,'raw':raw}]
        self.assertEqual(proof.revisions(cur,T,'tasks',[12],B),{})
    @patch.object(proof,'revisions',return_value={'12':'b'*64})
    def test_old_ui_revision_after_sync_fails_closed(self,revisions):
        cur=Mock();cur.fetchone.return_value={'raw':{'id':12}}
        with self.assertRaises(TaskFlowError) as exc:proof.require(cur,B,'tasks',12,'a'*64)
        self.assertEqual(exc.exception.code,'board_source_sync_required')
    @patch.object(proof,'revisions',return_value={'12':'a'*64})
    def test_mutation_locks_cached_row_before_proof_read(self,revisions):
        cur=Mock();cur.fetchone.return_value={'raw':{'id':12}}
        proof.require(cur,B,'tasks',12,'a'*64)
        self.assertIn('FOR UPDATE',cur.execute.call_args.args[0])
        self.assertEqual(revisions.call_args.kwargs['expected_raw'],{'12':{'id':12}})
        self.assertTrue(revisions.call_args.kwargs['lock_proof'])
    @patch.object(proof,'revisions',return_value={'12':'b'*64})
    def test_unknown_check_accepts_new_genuine_snapshot_same_binding(self,revisions):
        cur=Mock();cur.fetchone.return_value={'raw':{'id':12}}
        proof.require(cur,B,'tasks',12,'a'*64,allow_latest=True)
    @patch.object(proof,'ready',return_value=False)
    def test_missing_schema_has_no_request_time_ddl(self,ready):
        cur=Mock();self.assertEqual(proof.revisions(cur,T,'tasks',[12],B),{})
        cur.execute.assert_not_called()
    @patch('tenant_crm_import_provenance.validate_observation')
    @patch.object(proof,'ready',return_value=True)
    def test_record_uses_observed_binding_not_current_guess(self,ready,validate):
        cur=Mock();obs=Mock(binding=B,raw_digests=(('12',proof.digest({'id':12})),))
        proof.record(cur,T,'tasks',[{'raw':{'id':12}}],obs)
        validate.assert_called_once()
        row=cur.executemany.call_args.args[1][0]
        self.assertEqual(row[3:5],(B.account_id,B.connection_generation))
    @patch('tenant_crm_import_provenance.validate_observation',side_effect=RuntimeError('connection changed'))
    def test_reconnect_between_get_and_save_never_records(self,validate):
        cur=Mock()
        with self.assertRaises(RuntimeError):proof.record(cur,T,'tasks',[{'raw':{'id':12}}],Mock(binding=B))
        cur.executemany.assert_not_called()

if __name__=='__main__':unittest.main()
