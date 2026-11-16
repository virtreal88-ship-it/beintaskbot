"""Physical identity changes cannot detach durable operator ownership."""
import json
import uuid
from unittest.mock import MagicMock
import pytest
import tenant_connect_direct_reconcile as reconcile
from tenant_linear_policy import TenantLinearError
from test_tenant_support_inbox import A, CA, PROFILE, database, db


def seed(db, monkeypatch):
    from tenant_dialog_lifecycle import migrate
    monkeypatch.setattr(reconcile,'_connect',lambda:db)
    reconcile.ensure_schema(db)
    with db.cursor() as cur:migrate(cur)
    ids=['994501234567@s.whatsapp.net','80427641475293@lid']
    for conversation,lead in zip(ids,[5101,5102]):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,%s,'contact')",(A,CA,conversation,lead))
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,raw) VALUES(%s::uuid,%s::uuid,%s,%s,'connect:'||%s::text,'incoming',%s::jsonb)",(str(uuid.uuid4()),A,lead,'connect:'+conversation,CA,json.dumps({'conversationId':conversation,'directIdentity':dict(zip(['jid','lid'],ids))})))
    db.commit()
    candidate=reconcile.preview(PROFILE,CA)['items'][0]
    return ids,{'operation':'merge','pair_id':candidate['pair_id'],'precondition':candidate['precondition'],'request_id':str(uuid.uuid4()),'confirm':True}


@pytest.mark.parametrize('state',['waiting','accepted','closed','dismissed','episode_only'])
def test_affected_route_blocks_physical_rewrite(db,monkeypatch,state):
    ids,payload=seed(db,monkeypatch)
    actor=PROFILE['telegram_id']
    if state=='episode_only':
        db.execute('INSERT INTO saas_dialog_episodes(tenant_id,id,channel_id,conversation_id,lead_id,accepted_by,operator_id,closed_at) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,5101,%s,%s,now())',(A,str(uuid.uuid4()),CA,ids[0],actor,actor))
    else:
        db.execute('INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id) VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s)',(A,CA,ids[0],str(uuid.uuid4()),state,None if state=='waiting' else actor))
    db.commit()
    with pytest.raises(TenantLinearError,match='Объединение'):
        reconcile.apply(PROFILE,payload)
    assert db.execute('SELECT count(*) AS n FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND conversation_id=ANY(%s::text[])',(A,ids))[0]['n']==2


def test_pair_lock_precedes_dependency_table_locks(monkeypatch):
    import tenant_connect_dialog_equivalence as equivalence
    events=[];cur=MagicMock()
    cur.fetchone.return_value={'channel':CA,'state':'merged','snapshot':{'ids':['a','b']}}
    cur.execute.side_effect=lambda *args:events.append('mutex' if 'pg_advisory' in args[0] else 'read')
    monkeypatch.setattr(equivalence,'lock',lambda *a:events.append('aliases_routes'))
    monkeypatch.setattr(reconcile.dependencies,'lock',lambda *a:events.append('tables'))
    assert reconcile._lock_pair(cur,A,'pair')==(CA,['a','b'])
    assert events[-3:]==['mutex','aliases_routes','tables']


def test_unrelated_workflow_does_not_block_pair(db,monkeypatch):
    ids,payload=seed(db,monkeypatch)
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind) VALUES(%s::uuid,%s::uuid,'unrelated',5199,'contact')",(A,CA))
    db.execute("INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id) VALUES(%s::uuid,%s::uuid,'unrelated',%s::uuid,'accepted',%s)",(A,CA,str(uuid.uuid4()),PROFILE['telegram_id']))
    db.commit()
    assert reconcile.apply(PROFILE,payload)['state']=='merged'


def test_canonical_workflow_blocks_physical_rollback(db,monkeypatch):
    ids,payload=seed(db,monkeypatch)
    assert reconcile.apply(PROFILE,payload)['state']=='merged'
    current=reconcile.preview(PROFILE,CA)['items'][0]
    db.execute("INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id) VALUES(%s::uuid,%s::uuid,%s,%s::uuid,'closed',%s)",(A,CA,ids[0],str(uuid.uuid4()),PROFILE['telegram_id']))
    db.commit()
    with pytest.raises(TenantLinearError,match='Объединение'):
        reconcile.apply(PROFILE,{**payload,'operation':'rollback','precondition':current['precondition'],'request_id':str(uuid.uuid4())})
    assert db.execute('SELECT count(*) AS n FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND conversation_id=ANY(%s::text[])',(A,ids))[0]['n']==1
