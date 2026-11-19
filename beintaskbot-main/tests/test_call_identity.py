import pytest
from test_call_sources import managed
from test_browser_calls import gateway,snapshot,provider
from test_call_recordings import pilot
from test_tenant_support_inbox import database,db,A,B,CA,CB,PROFILE
import tenant_browser_calls as calls
import tenant_call_identity as identity
from tenant_linear_policy import TenantLinearError

LID='111111111111111@lid';PN='994100000777@s.whatsapp.net'
CALLER={'phone_jid':PN,'display_name':'Synthetic Caller'}

def incoming(**extra):return {**provider('ringing',LID,'inbound','identity-call'),**extra}

def test_incoming_verified_caller_and_missed_history_keep_original_peer(managed):
    db,key,cfg=managed
    calls.sync(cfg,snapshot(2,active=[incoming(caller=CALLER)]))
    row=db.execute("SELECT * FROM saas_browser_calls WHERE provider_id='identity-call'")[0]
    assert row['peer_jid']==LID and row['conversation_id']==PN
    dialog=db.execute('SELECT phone,display_name FROM saas_connect_conversations WHERE tenant_id=%s AND connect_channel_id=%s AND conversation_id=%s',(A,CA,PN))[0]
    assert dialog=={'phone':PN.split('@')[0],'display_name':'Synthetic Caller'}
    ended={**incoming(caller=CALLER),'state':'ended'}
    calls.sync(cfg,snapshot(3,ended=[ended]));calls.sync(cfg,snapshot(4,ended=[ended]))
    assert db.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE message_type='call_missed'")[0]['n']==1
    assert not db.execute('SELECT * FROM saas_connect_conversations WHERE tenant_id=%s AND conversation_id=%s',(B,PN));db.commit()

def test_late_identity_enriches_same_lid_without_moving_history_or_manual_name(managed):
    db,key,cfg=managed
    calls.sync(cfg,snapshot(2,active=[incoming()]))
    before=db.execute("SELECT id,lead_id,peer_jid,conversation_id FROM saas_browser_calls WHERE provider_id='identity-call'")[0]
    db.execute("UPDATE saas_connect_conversations SET manual_display_name='Owner choice' WHERE tenant_id=%s AND conversation_id=%s",(A,LID));db.commit()
    calls.sync(cfg,snapshot(3,active=[incoming(caller=CALLER)]))
    assert db.execute("SELECT id,lead_id,peer_jid,conversation_id FROM saas_browser_calls WHERE provider_id='identity-call'")[0]==before
    row=db.execute('SELECT phone,display_name,manual_display_name FROM saas_connect_conversations WHERE tenant_id=%s AND conversation_id=%s',(A,LID))[0]
    assert row=={'phone':PN.split('@')[0],'display_name':'Synthetic Caller','manual_display_name':'Owner choice'};db.commit()

def test_conflicting_two_existing_histories_are_not_joined(managed):
    db,key,cfg=managed
    calls.sync(cfg,snapshot(2,active=[incoming()]))
    from tenant_connect_transport import _upsert_conversation
    with db.cursor() as cur:_upsert_conversation(cur,A,CA,PN,{'kind':'contact','display_name':'Different history'})
    db.commit()
    before=db.execute('SELECT conversation_id,lead_id,phone,display_name FROM saas_connect_conversations ORDER BY lead_id');db.commit()
    calls.sync(cfg,snapshot(3,active=[incoming(caller=CALLER)]))
    assert db.execute('SELECT conversation_id,lead_id,phone,display_name FROM saas_connect_conversations ORDER BY lead_id')==before
    assert not db.execute('SELECT * FROM saas_connect_direct_identities WHERE identity_key=%s',(LID,));db.commit()

@pytest.mark.parametrize('caller',[{'phone_jid':LID,'display_name':'Guess'}, {**CALLER,'display_name':'bad\nname'}, {**CALLER,'extra':'no'}, {**CALLER,'phone_jid':'https://invalid'}])
def test_invalid_caller_evidence_fails_before_call_creation(managed,caller):
    db,key,cfg=managed
    with pytest.raises(TenantLinearError):calls.sync(cfg,snapshot(2,active=[incoming(caller=caller)]))
    assert not db.execute("SELECT id FROM saas_browser_calls WHERE provider_id='identity-call'");db.commit()

def test_phone_peer_cannot_claim_another_phone():
    assert not identity.valid('994100000888@s.whatsapp.net',CALLER)
    assert identity.valid(LID,CALLER)


def test_addressed_repair_requires_exact_private_scope_and_preserves_call(managed,monkeypatch):
    from unittest.mock import Mock
    import tenant_call_sources as sources
    import tenant_whatsapp_native_transport as transport
    db,key,cfg=managed
    calls.sync(cfg,snapshot(2,active=[incoming()]))
    calls.sync(cfg,snapshot(3,ended=[{**incoming(),'state':'ended'}]))
    row=db.execute("SELECT * FROM saas_browser_calls WHERE provider_id='identity-call'")[0];db.commit()
    call_id=str(row['id'])
    binding={'gateway_id':cfg['gateway_id'],'scope':{'tenant':A,'source':CA,'generation':'synthetic','account':cfg['account_jid']}}
    monkeypatch.setattr(transport,'bind',lambda cur,route:{**route,'native':binding})
    remote=Mock(return_value={'scope':{**binding['scope'],'source':CB},'peer_jid':LID,'caller':CALLER})
    monkeypatch.setattr(sources,'remote',remote)
    with pytest.raises(TenantLinearError):identity.refresh_call(PROFILE,call_id)
    with pytest.raises(TenantLinearError):identity.refresh_call({**PROFILE,'tenant_id':B},call_id)
    assert remote.call_count==1
    remote.return_value={'scope':binding['scope'],'peer_jid':LID,'caller':CALLER}
    assert identity.refresh_call(PROFILE,call_id)['updated']
    assert identity.refresh_call(PROFILE,call_id)['updated']
    assert db.execute("SELECT * FROM saas_browser_calls WHERE provider_id='identity-call'")[0]==row
    assert db.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE message_type='call_missed'")[0]['n']==1;db.commit()
