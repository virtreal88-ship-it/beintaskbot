"""Synthetic handoff only. No browser/provider calls or customer data."""
import copy
import json
from cryptography.fernet import Fernet
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, PROFILE
from test_call_recordings import pilot
from test_browser_calls import gateway
from test_call_sources import managed, MANAGED, TOKEN
import tenant_whatsapp_native_inbox as inbox
from tenant_linear_policy import TenantLinearError


def delivery(**changes):
    return {'version':1,'scope':{'tenant':A,'source':CA,'account':'123456789@s.whatsapp.net','generation':'test-generation-1'},
            'after':0,'event':{'sequence':2,'id':'a'*64,'payload':{'kind':'message','text':'SYNTHETIC_PRIVATE_TEXT','proto':'synthetic-key-material'}},
            'proof':'b'*64,**changes}


def raw(value):
    return json.dumps(value).encode()


@pytest.fixture
def capture(managed,monkeypatch):
    db,key,cfg=managed
    monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_INBOX','1')
    monkeypatch.setattr(inbox,'_connect',lambda:db)
    cipher=Fernet(Fernet.generate_key())
    monkeypatch.setattr(inbox,'_fernet',lambda:cipher)
    with db.cursor() as cur:inbox.migrate(cur)
    db.execute('''INSERT INTO saas_whatsapp_capture_generations
      (tenant_id,channel_id,generation,gateway_id,account_jid,enabled)
      VALUES(%s,%s,'test-generation-1',%s,%s,TRUE)''',(A,CA,MANAGED,cfg['account_jid']))
    db.commit()
    yield db,cipher,cfg
    db.execute('DELETE FROM saas_whatsapp_native_inbox')
    db.execute('DELETE FROM saas_whatsapp_capture_generations');db.commit()


def store(value):
    return inbox.store_delivery('Bearer '+TOKEN,raw(value))


def test_disabled_before_credentials_or_database(monkeypatch):
    monkeypatch.delenv('BEINTASKBOT_UNIFIED_WHATSAPP_INBOX',raising=False)
    with pytest.raises(TenantLinearError) as error:inbox.store_delivery('Bearer not-a-key',b'{}')
    assert error.value.status==404


@pytest.mark.parametrize('mutate',[
    lambda d:d.update(version=True),lambda d:d.update(after=True),lambda d:d.update(after=2),
    lambda d:d.update(proof='invalid'),lambda d:d.update(extra='not accepted'),
    lambda d:d['scope'].update(source='not-a-uuid'),lambda d:d['scope'].update(generation='../other'),
    lambda d:d['scope'].update(account='12345@lid'),lambda d:d['event'].update(payload=[]),
    lambda d:d['event'].update(sequence=2**63),lambda d:d['event'].update(id='invalid'),
])
def test_delivery_validation(mutate):
    value=delivery();mutate(value)
    with pytest.raises(TenantLinearError):inbox.validate(raw(value))


def test_duplicate_json_and_nonfinite_numbers_rejected():
    value=raw(delivery())
    for bad in (value.replace(b'"after": 0',b'"after": 0, "after": 1'),
                value.replace(b'"after": 0',b'"after": NaN'), b' '* (inbox.MAX_DELIVERY+1)):
        with pytest.raises(TenantLinearError):inbox.validate(bad)


def test_commit_retry_encryption_and_sparse_cursor(capture):
    db,cipher,cfg=capture
    value=delivery();ack=store(value)
    assert ack['state']=='stored' and ack['event_id']==value['event']['id'] and ack['proof']==value['proof']
    assert store(value)==ack
    row=db.execute('SELECT * FROM saas_whatsapp_native_inbox')[0];db.commit()
    assert b'SYNTHETIC_PRIVATE_TEXT' not in bytes(row['encrypted_payload'])
    decoded=json.loads(cipher.decrypt(bytes(row['encrypted_payload'])))
    assert decoded=={'scope':value['scope'],'event_id':value['event']['id'],'payload':value['event']['payload']}
    second=delivery(after=2,event={'sequence':9,'id':'c'*64,'payload':{'kind':'receipt'}})
    assert store(second)['sequence']==9
    assert store(value)==ack  # delayed duplicate does not rewind cursor
    assert db.execute('SELECT stored_sequence FROM saas_whatsapp_capture_generations')[0]['stored_sequence']==9
    assert len(db.execute('SELECT event_id FROM saas_whatsapp_native_inbox'))==2
    db.commit()


@pytest.mark.parametrize('field,value',[('tenant',B),('source',CB),('account','987654321@s.whatsapp.net'),('generation','new-unregistered')])
def test_scope_and_generation_cannot_be_supplied_by_sender(capture,field,value):
    db,_,_=capture
    request=delivery();request['scope'][field]=value
    with pytest.raises(TenantLinearError):store(request)
    assert not db.execute('SELECT event_id FROM saas_whatsapp_native_inbox');db.commit()


def test_conflicting_replay_and_wrong_cursor_fail_closed(capture):
    db,_,_=capture;value=delivery();store(value)
    for mutate in (lambda d:d['event']['payload'].update(text='changed'),
                   lambda d:d['event'].update(sequence=3),lambda d:d.update(after=1)):
        changed=copy.deepcopy(value);mutate(changed)
        with pytest.raises(TenantLinearError) as error:store(changed)
        assert error.value.status==409
    skipped=delivery(after=8,event={'sequence':9,'id':'c'*64,'payload':{}})
    with pytest.raises(TenantLinearError):store(skipped)
    assert len(db.execute('SELECT event_id FROM saas_whatsapp_native_inbox'))==1;db.commit()


@pytest.mark.parametrize('sql',[
    'UPDATE saas_connect_channels SET active=FALSE',
    'UPDATE saas_browser_call_sources SET enabled=FALSE',
    "UPDATE saas_browser_call_sources SET token_sha256='revoked'",
    'UPDATE saas_whatsapp_capture_generations SET enabled=FALSE',
])
def test_fresh_source_revocation_after_auth_snapshot(capture,monkeypatch,sql):
    db,_,cfg=capture
    import tenant_browser_calls as calls
    monkeypatch.setattr(calls,'gateway_auth',lambda _:cfg)
    db.execute(sql);db.commit()
    with pytest.raises(TenantLinearError):store(delivery())
    assert not db.execute('SELECT event_id FROM saas_whatsapp_native_inbox');db.commit()


@pytest.mark.parametrize('failure',["RAISE EXCEPTION 'synthetic cursor failure';",'RETURN NULL;'])
def test_cursor_write_failure_rolls_back_event_and_returns_no_receipt(capture,failure):
    db,_,_=capture
    db.execute('''CREATE FUNCTION test_fail_native_cursor() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN '''+failure+''' END; $$''')
    db.execute('''CREATE TRIGGER test_native_cursor_failure BEFORE UPDATE ON saas_whatsapp_capture_generations
      FOR EACH ROW EXECUTE FUNCTION test_fail_native_cursor()''');db.commit()
    try:
        with pytest.raises(Exception):store(delivery())
        assert not db.execute('SELECT event_id FROM saas_whatsapp_native_inbox')
        assert db.execute('SELECT stored_sequence FROM saas_whatsapp_capture_generations')[0]['stored_sequence']==0
        db.commit()
    finally:
        db.execute('DROP TRIGGER test_native_cursor_failure ON saas_whatsapp_capture_generations')
        db.execute('DROP FUNCTION test_fail_native_cursor()');db.commit()


def test_unknown_commit_replays_one_row(capture,monkeypatch):
    db,_,_=capture
    class LostReply:
        def __enter__(self):return db
        def __exit__(self,kind,*_):
            if kind:db.rollback()
            else:
                db.commit()
                raise OSError('synthetic lost response after commit')
    monkeypatch.setattr(inbox,'_connect',LostReply)
    with pytest.raises(OSError):store(delivery())
    monkeypatch.setattr(inbox,'_connect',lambda:db)
    assert store(delivery())['state']=='stored'
    assert len(db.execute('SELECT event_id FROM saas_whatsapp_native_inbox'))==1;db.commit()
