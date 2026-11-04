"""Offline transaction contract only; not evidence of PostgreSQL locking."""
import uuid

import pytest

from tenant_linear_policy import TenantLinearError
import tenant_voice_test_grant as grant


class Cursor:
    def __init__(self,used=0):
        self.used=used;self.escrow=None;self.result=None

    def execute(self,sql,args=()):
        if 'FROM saas_voice_test_session_escrow' in sql:
            self.result=self.escrow
        elif 'FROM saas_voice_test_hold' in sql:
            self.result={'used':self.used}
        elif 'INSERT INTO saas_voice_test_session_escrow' in sql:
            _,session_id,fingerprint,amount=args
            assert self.escrow is None
            self.escrow={'session_id':session_id,'fingerprint':fingerprint,
                         'reserved_microusd':amount,'state':'reserved'}
            self.result=None
        else:
            raise AssertionError('unexpected SQL')

    def fetchone(self):return self.result


def identity(actor=7):
    return (str(uuid.uuid4()),actor,str(uuid.uuid4()),'synthetic-dialog'),str(uuid.uuid4())


def reserve_locked(cur,scope,session_id):
    validated=grant._session_scope(scope,session_id)
    return grant._reserve_session_locked(cur,scope,session_id,validated)


def test_full_cap_cannot_be_reused_even_for_same_session():
    cur=Cursor();scope,session_id=identity()
    first=reserve_locked(cur,scope,session_id)
    assert first=={'state':'reserved','scope':scope,'session_id':session_id,'reserved_microusd':5_000_000}
    with pytest.raises(TenantLinearError):reserve_locked(cur,scope,session_id)
    with pytest.raises(TenantLinearError):reserve_locked(cur,scope,str(uuid.uuid4()))
    with pytest.raises(TenantLinearError):reserve_locked(cur,(scope[0],8,scope[2],scope[3]),session_id)


@pytest.mark.parametrize('used',[1,5_000_000,None,-1])
def test_prior_or_unknown_usage_never_reserves_another_full_cap(used):
    cur=Cursor(used);scope,session_id=identity()
    with pytest.raises(TenantLinearError):reserve_locked(cur,scope,session_id)
    assert cur.escrow is None


def test_unknown_escrow_never_becomes_a_fresh_reservation():
    cur=Cursor();scope,session_id=identity();reserve_locked(cur,scope,session_id)
    cur.escrow['state']='unknown'
    with pytest.raises(TenantLinearError):reserve_locked(cur,scope,session_id)


def test_response_claim_requires_the_same_scoped_session_escrow():
    cur=Cursor();scope,session_id=identity();reserve_locked(cur,scope,session_id)
    data=grant._session_scope(scope,session_id)
    grant._require_session_escrow(cur,data)
    with pytest.raises(TenantLinearError):grant._require_session_escrow(cur,{**data,'actor_id':8})
