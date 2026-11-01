import json
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB
from tenant_operator_dialogs import _attach_verified_phone,_message


def test_media_version_is_safe_and_changes_only_with_source_identity():
    base={'id':'row','channel':'connect:'+CA,'direction':'incoming','message_type':'audio','raw':{'mediaId':'one','url':'https://private.invalid','voice_transcription':{'status':'failed'}}}
    first=_message(base);second=_message({**base,'raw':{**base['raw'],'voice_transcription':{'status':'completed'}}})
    assert first['media_version']==second['media_version'] and len(first['media_version'])==64
    assert first['media_version']!=_message({**base,'raw':{'mediaId':'two'}})['media_version']
    assert 'private.invalid' not in json.dumps(first) and 'raw' not in first


@pytest.mark.parametrize('phones,expected',[([],''),(['994500001234@s.whatsapp.net'],'+994500001234'),(['994500001234@s.whatsapp.net','994500005678@s.whatsapp.net'],'')])
def test_only_unique_verified_channel_scoped_phone_alias_is_used(db,phones,expected):
    db.execute('CREATE TABLE IF NOT EXISTS saas_connect_direct_identities(tenant_id UUID,connect_channel_id UUID,identity_key TEXT,canonical_conversation_id TEXT)')
    lid='123456789012345@lid'
    for tenant,channel in ((A,CA),(B,CB)):
        db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id) VALUES(%s::uuid,%s::uuid) ON CONFLICT DO NOTHING",(tenant,channel))
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) VALUES(%s::uuid,%s::uuid,%s) ON CONFLICT DO NOTHING",(tenant,channel,lid))
    for key in phones:db.execute('INSERT INTO saas_connect_direct_identities(tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES(%s::uuid,%s::uuid,%s,%s)',(A,CA,key,lid))
    db.execute('INSERT INTO saas_connect_direct_identities(tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES(%s::uuid,%s::uuid,%s,%s)',(B,CB,'994500009999@s.whatsapp.net',lid));db.commit()
    dialog={'channel_id':CA,'conversation_id':lid,'phone':'','customer_kind':'contact'}
    with db.cursor() as cur:_attach_verified_phone(cur,A,dialog)
    assert dialog['phone']==expected
    assert '123456789012345' not in dialog['phone']
