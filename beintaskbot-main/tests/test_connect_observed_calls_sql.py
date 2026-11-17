import pytest
import uuid
from test_connect_call_corrections_sql import A,B,CA,CB,database,db,flow,channels,ingest
from test_connect_missed_calls import call,rejected
from test_connect_call_corrections import invalidated
from test_connect_observed_calls import observed
import tenant_connect_transport as transport

@pytest.mark.parametrize('factory',[call,rejected,invalidated])
@pytest.mark.parametrize('reverse',[False,True])
def test_observed_both_orders_retries_and_isolation(ingest,channels,factory,reverse):
    events=[observed(channelId=CA),factory(channelId=CA,source='evolution')]
    if reverse:events.reverse()
    for event in events*2:assert ingest({**event,'id':str(uuid.uuid4())})
    rows=channels['db'].execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id LIKE 'connect:call:%%'",(A,))
    assert len(rows)==1 and rows[0]['message_type']=='call_'+factory()['data']['callStatus']
    assert rows[0]['direction']=='system'
    assert ingest(observed(channelId=CB))
    rows=channels['db'].execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id LIKE 'connect:call:%%'",(A,))
    assert len(rows)==2
    assert not channels['db'].execute("SELECT 1 FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id LIKE 'connect:call:%%'",(B,))

@pytest.mark.parametrize('changes',[{'isVideo':True},{'conversationId':'994700000099@s.whatsapp.net'}])
def test_observed_mismatched_known_identity(ingest,channels,changes):
    assert ingest(observed(channelId=CA))
    with pytest.raises(transport.ConnectWebhookError,match='Call scope mismatch'):
        ingest(rejected(channelId=CA,source='evolution',**changes))
    rows=channels['db'].execute("SELECT message_type FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id LIKE 'connect:call:%%'",(A,))
    assert len(rows)==1 and rows[0]['message_type']=='call_observed'


from itertools import permutations
from test_connect_observed_calls import observed_invalidated

@pytest.mark.parametrize('order',list(permutations([observed,observed_invalidated])))
def test_observation_retraction_reordered(ingest,channels,order):
    for factory in (*order,*order):assert ingest(factory(channelId=CA))
    rows=channels['db'].execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id LIKE 'connect:call:%%'",(A,))
    assert len(rows)==(1 if order[0] is observed else 0)
    if rows:assert rows[0]['raw']['correctionOf']=='call.observed' and rows[0]['message_type']=='call_invalidated'
    assert len(channels['db'].execute('SELECT 1 FROM saas_connect_call_retractions WHERE tenant_id=%s::uuid',(A,)))==1

@pytest.mark.parametrize('factory',[call,rejected,invalidated])
@pytest.mark.parametrize('order',list(permutations(range(3))))
def test_observation_retraction_never_erases_proven_evidence(ingest,channels,factory,order):
    events=[observed(channelId=CA),observed_invalidated(channelId=CA),factory(channelId=CA,source='evolution')]
    for index in (*order,*order):assert ingest({**events[index],'id':str(uuid.uuid4())})
    rows=channels['db'].execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id LIKE 'connect:call:%%'",(A,))
    assert len(rows)==1 and rows[0]['message_type']=='call_'+factory()['data']['callStatus']
    assert rows[0]['raw'].get('correctionOf')==factory()['data'].get('correctionOf')


def test_retraction_bypasses_later_route_conflict(ingest,channels,monkeypatch):
    assert ingest(observed(channelId=CA))
    def forbidden(*args,**kwargs):raise AssertionError('No identity resolution or route creation allowed')
    monkeypatch.setattr(transport,'_canonical_direct_conversation',forbidden)
    monkeypatch.setattr(transport,'_upsert_conversation',forbidden)
    assert ingest(observed_invalidated(channelId=CA))
    assert ingest(observed(channelId=CA))
    rows=channels['db'].execute("SELECT message_type FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id LIKE 'connect:call:%%'",(A,))
    assert len(rows)==1 and rows[0]['message_type']=='call_invalidated'


def test_unknown_retraction_is_durable_without_route_or_lead(ingest,channels,monkeypatch):
    local=channels['db']
    before=local.execute('SELECT tenant_id,connect_channel_id,conversation_id,lead_id FROM saas_connect_conversations ORDER BY lead_id')
    def forbidden(*args,**kwargs):raise AssertionError('No resolution or route creation')
    monkeypatch.setattr(transport,'_canonical_direct_conversation',forbidden)
    monkeypatch.setattr(transport,'_upsert_conversation',forbidden)
    assert ingest(observed_invalidated(channelId=CA))
    assert ingest(observed(channelId=CA))
    assert ingest(observed_invalidated(channelId=CA))
    assert local.execute('SELECT tenant_id,connect_channel_id,conversation_id,lead_id FROM saas_connect_conversations ORDER BY lead_id')==before
    assert not local.execute("SELECT 1 FROM saas_crm_messages WHERE external_id LIKE 'connect:call:%%'")
    assert len(local.execute('SELECT 1 FROM saas_connect_call_retractions WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(A,CA)))==1


@pytest.mark.parametrize('changes',[{'isVideo':True},{'conversationId':'994700000099@s.whatsapp.net'},
    {'offerOccurredAt':'2026-11-15T08:59:44Z'}])
def test_wrong_retraction_scope_rolls_back_ledger(ingest,channels,changes):
    assert ingest(observed(channelId=CA))
    with pytest.raises(transport.ConnectWebhookError,match='Call scope mismatch'):
        ingest(observed_invalidated(channelId=CA,**changes))
    assert not channels['db'].execute('SELECT 1 FROM saas_connect_call_retractions')
    rows=channels['db'].execute("SELECT message_type FROM saas_crm_messages WHERE external_id LIKE 'connect:call:%%'")
    assert len(rows)==1 and rows[0]['message_type']=='call_observed'


def test_retraction_channel_and_tenant_are_authenticated(ingest,channels):
    assert ingest(observed(channelId=CA))
    assert ingest(observed_invalidated(channelId=str(uuid.uuid4()))) is False
    assert not channels['db'].execute('SELECT 1 FROM saas_connect_call_retractions')
    assert ingest(observed_invalidated(channelId=CB))
    assert not channels['db'].execute('SELECT 1 FROM saas_connect_call_retractions WHERE tenant_id=%s::uuid',(B,))
    rows=channels['db'].execute("SELECT message_type FROM saas_crm_messages WHERE external_id LIKE 'connect:call:%%'")
    assert len(rows)==1 and rows[0]['message_type']=='call_observed'


def test_retraction_matches_original_provider_route_after_canonicalization(ingest,channels,monkeypatch):
    lid='80000000000091@lid';pn='994700000091@s.whatsapp.net';local=channels['db']
    # Previously authenticated source-local identity mapping, not name/phone inference.
    local.execute('INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) VALUES(%s::uuid,%s::uuid,%s)',(A,CA,pn))
    local.execute('''INSERT INTO saas_connect_direct_identities(tenant_id,connect_channel_id,identity_key,canonical_conversation_id)
        VALUES(%s::uuid,%s::uuid,%s,%s)''',(A,CA,lid,pn));local.commit()
    assert ingest(observed(channelId=CA,conversationId=lid))
    row=local.execute("SELECT raw FROM saas_crm_messages WHERE message_type='call_observed'")[0]['raw']
    assert row['conversationId']==pn and row['_crm_provider_conversation_id']==lid
    def conflict(*args,**kwargs):raise AssertionError('Must not resolve conflicting aliases')
    monkeypatch.setattr(transport,'_canonical_direct_conversation',conflict)
    assert ingest(observed_invalidated(channelId=CA,conversationId=lid))
    row=local.execute("SELECT raw FROM saas_crm_messages WHERE message_type='call_invalidated'")[0]['raw']
    assert row['conversationId']==pn and row['_crm_provider_conversation_id']==lid


@pytest.mark.parametrize('factory',[call,rejected,invalidated])
@pytest.mark.parametrize('changes',[{'isVideo':True},{'conversationId':'994700000099@s.whatsapp.net'},{'source':'other'}])
def test_ledger_first_known_must_match_immutable_scope(ingest,channels,factory,changes):
    assert ingest(observed_invalidated(channelId=CA))
    local=channels['db']
    before=local.execute('SELECT tenant_id,connect_channel_id,conversation_id,lead_id FROM saas_connect_conversations ORDER BY lead_id')
    event=factory(channelId=CA,source='evolution');event['data'].update(changes)
    with pytest.raises(transport.ConnectWebhookError,match='Call scope mismatch'):ingest(event)
    assert local.execute('SELECT tenant_id,connect_channel_id,conversation_id,lead_id FROM saas_connect_conversations ORDER BY lead_id')==before
    assert not local.execute("SELECT 1 FROM saas_crm_messages WHERE external_id LIKE 'connect:call:%%'")
    assert len(local.execute('SELECT 1 FROM saas_connect_call_retractions'))==1


@pytest.mark.parametrize('factory',[call,rejected,invalidated])
def test_ledger_is_scoped_not_cross_channel_or_tenant_authority(ingest,channels,factory):
    assert ingest(observed_invalidated(channelId=CA))
    assert ingest(factory(channelId=CB,source='evolution',conversationId='994700000099@s.whatsapp.net',isVideo=True))
    rows=channels['db'].execute("SELECT channel,tenant_id FROM saas_crm_messages WHERE external_id LIKE 'connect:call:%%'")
    assert len(rows)==1 and rows[0]['channel']=='connect:'+CB and str(rows[0]['tenant_id'])==A
    assert not channels['db'].execute('SELECT 1 FROM saas_connect_call_retractions WHERE tenant_id=%s::uuid',(B,))


@pytest.mark.parametrize('changes',[{'directIdentity':{'jid':'994700000099@s.whatsapp.net','lid':'80000000000091@lid'}},
    {'originalConversationId':'80000000000099@lid'}])
def test_retraction_pair_and_original_route_are_immutable(ingest,channels,changes):
    original={'channelId':CA,'conversationId':'80000000000091@lid',
        'directIdentity':{'jid':'994700000091@s.whatsapp.net','lid':'80000000000091@lid'}}
    assert ingest(observed(**original))
    event=observed_invalidated(**original);event['data'].update(changes)
    with pytest.raises(transport.ConnectWebhookError,match='Call scope mismatch'):ingest(event)
    assert not channels['db'].execute('SELECT 1 FROM saas_connect_call_retractions')
    assert ingest(observed_invalidated(**original))
    assert ingest(observed(**original))
    event=observed(**original);event['data'].update(changes)
    with pytest.raises(transport.ConnectWebhookError,match='Call scope mismatch'):ingest(event)


@pytest.mark.parametrize('factory',[call,rejected,invalidated,observed,observed_invalidated])
def test_all_call_types_lock_before_identity_resolution(ingest,channels,monkeypatch,factory):
    import tenant_connect_call_retractions as retractions
    order=[];real_lock=retractions.lock;real_resolve=transport._canonical_direct_conversation
    def locked(*args):
        order.append('call');return real_lock(*args)
    def resolved(*args):
        assert order==['call'];order.append('alias');return real_resolve(*args)
    monkeypatch.setattr(retractions,'lock',locked)
    monkeypatch.setattr(transport,'_canonical_direct_conversation',resolved)
    assert ingest(factory(channelId=CA,source='evolution'))
    assert order==(['call'] if factory is observed_invalidated else ['call','alias'])
