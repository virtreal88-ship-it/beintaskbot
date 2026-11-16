import unittest
from tenant_dialog_rollout import RolloutState, guard, transition, validate_transition
from tenant_linear_policy import TenantLinearError
T='00000000-0000-0000-0000-000000000001'
class Cursor:
    def __init__(self,*rows): self.rows=list(rows); self.calls=[]
    def execute(self,sql,params=None): self.calls.append((sql,params))
    def fetchone(self): return self.rows.pop(0)
class Tests(unittest.TestCase):
    def test_first_activation_atomic(self):
        with self.assertRaises(TenantLinearError): validate_transition(RolloutState(),'active',False,0)
        cur=Cursor({'acquired':True},None)
        s=transition(cur,T,mode='active',admission_enabled=True,expected_generation=0)
        self.assertTrue(s.enforced and s.admission_enabled)
        self.assertEqual(s.generation,1)
        self.assertEqual(len(cur.calls),3)
        self.assertIn('COALESCE(saas_dialog_rollout.activated_at',cur.calls[2][0])
    def test_latch(self):
        s=RolloutState('active',True,True,3)
        for m in ('legacy','shadow'):
            with self.assertRaises(TenantLinearError): validate_transition(s,m,False,3)
        validate_transition(s,'active',False,3)
    def test_generation_types(self):
        for m,a,g in [('active',True,1),('active',True,False),('legacy',True,0),('shadow',1,0),('oops',False,0)]:
            with self.assertRaises(TenantLinearError): validate_transition(RolloutState(),m,a,g)
    def test_shadow(self):
        c=Cursor({'acquired':True},{'mode':'shadow','activated_at':None,'admission_enabled':False,'generation':1})
        self.assertFalse(guard(c,T).enforced)
        self.assertIn('pg_try_advisory_xact_lock_shared',c.calls[0][0])
    def test_legacy_row(self):
        c=Cursor({'acquired':True},None)
        self.assertEqual(guard(c,T),RolloutState())
        self.assertNotIn('to_regclass',c.calls[1][0])
    def test_bridge_explicit(self):
        c=Cursor({'acquired':True},{'ready':False})
        self.assertEqual(guard(c,T,allow_unmigrated=True),RolloutState())
        self.assertEqual(len(c.calls),2)
    def test_busy_lock(self):
        for f in [lambda c:guard(c,T),lambda c:transition(c,T,mode='active',admission_enabled=True,expected_generation=0)]:
            c=Cursor({'acquired':False})
            with self.assertRaises(TenantLinearError): f(c)
            self.assertEqual(len(c.calls),1)
    def test_schema_failure_propagates(self):
        class Broken(Cursor):
            def execute(self,sql,params=None):
                super().execute(sql,params)
                if 'FROM saas_dialog_rollout' in sql: raise RuntimeError('schema unavailable')
        with self.assertRaises(RuntimeError): guard(Broken({'acquired':True}),T)
if __name__=='__main__': unittest.main()
