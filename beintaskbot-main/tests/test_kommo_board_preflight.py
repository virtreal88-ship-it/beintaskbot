import unittest
from unittest.mock import MagicMock,patch
from scripts import prepare_kommo_board_provenance as script

class Preflight(unittest.TestCase):
    def connection(self):
        conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value={'acquired':True}
        return conn,cur
    @patch.object(script,'validate',return_value=False)
    def test_default_read_only_never_migrates(self,validate):
        conn,cur=self.connection()
        with patch('tenant_kommo_board_provenance.migrate') as migration:
            self.assertEqual(script.run(conn)['status'],'missing')
            migration.assert_not_called()
        statements=[c.args[0] for c in cur.execute.call_args_list]
        self.assertIn('SET TRANSACTION READ ONLY',statements)
        self.assertFalse(any('advisory' in s for s in statements))
    @patch.object(script,'validate',side_effect=[False,True])
    def test_apply_only_narrow_ddl_bounded_and_verified(self,validate):
        conn,cur=self.connection()
        with patch('tenant_kommo_board_provenance.migrate') as migration:
            self.assertTrue(script.run(conn,apply=True)['changed']);migration.assert_called_once_with(cur)
        sql='\n'.join(c.args[0] for c in cur.execute.call_args_list)
        self.assertIn("lock_timeout='3s'",sql);self.assertIn("statement_timeout='15s'",sql)
        self.assertIn('pg_try_advisory_xact_lock',sql)
    @patch.object(script,'validate')
    def test_busy_aborts_without_ddl(self,validate):
        conn,cur=self.connection();cur.fetchone.return_value={'acquired':False}
        with self.assertRaisesRegex(RuntimeError,'platform_schema_busy'):script.run(conn,apply=True)
        validate.assert_not_called()
    def test_incompatible_columns_rejected(self):
        cur=MagicMock();cur.fetchone.return_value={'present':True};cur.fetchall.return_value=[]
        with self.assertRaisesRegex(RuntimeError,'schema_mismatch'):script.validate(cur)
    def test_compatible_types_but_missing_constraints_rejected(self):
        cur=MagicMock();cur.fetchone.return_value={'present':True}
        cur.fetchall.side_effect=[[dict(column_name=k,data_type=v[0],is_nullable=v[1]) for k,v in script.COLUMNS.items()],[]]
        with self.assertRaisesRegex(RuntimeError,'primary_key_mismatch'):script.validate(cur)

if __name__=='__main__':unittest.main()
