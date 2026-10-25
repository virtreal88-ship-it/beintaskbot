import json
import uuid

from test_tenant_support_inbox import A, B, CA, PROFILE, database, db
import tenant_connect_direct_reconcile as reconcile


def test_owner_preview_merge_and_rollback_preserve_message_ids(db, monkeypatch):
    monkeypatch.setattr(reconcile,'_connect',lambda:db)
    reconcile.ensure_schema(db)
    jid,lid='994501234567@s.whatsapp.net','80427641475293@lid'
    for conversation,lead in ((jid,5101),(lid,5102)):
        db.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind)
          VALUES(%s::uuid,%s::uuid,%s,%s,'contact')''',(A,CA,conversation,lead))
        db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,raw)
          VALUES(%s::uuid,%s::uuid,%s,%s,'connect:'||%s::text,'incoming',%s::jsonb)''',
          (str(uuid.uuid4()),A,lead,'connect:'+conversation,CA,json.dumps({'conversationId':conversation,'directIdentity':{'jid':jid,'lid':lid}})))
    db.commit()
    preview=reconcile.preview(PROFILE,CA); candidate=next(x for x in preview['items'] if x['eligible'])
    before=db.execute("SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s::bigint[]) ORDER BY id",(A,[5101,5102]));db.commit()
    merged=reconcile.apply(PROFILE,{'operation':'merge','pair_id':candidate['pair_id'],'precondition':candidate['precondition'],'request_id':str(uuid.uuid4()),'confirm':True})
    assert merged['state']=='merged' and merged['moved_messages']==1
    assert db.execute('SELECT count(*) AS n FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s',(A,CA,lid))[0]['n']==0
    current=reconcile.preview(PROFILE,CA)['items'][0]
    rolled=reconcile.apply(PROFILE,{'operation':'rollback','pair_id':candidate['pair_id'],'precondition':current['precondition'],'request_id':str(uuid.uuid4()),'confirm':True})
    assert rolled['state']=='rolled_back'
    assert db.execute("SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s::bigint[]) ORDER BY id",(A,[5101,5102]))==before


def test_reconciliation_never_matches_another_channel_or_tenant(db, monkeypatch):
    monkeypatch.setattr(reconcile,'_connect',lambda:db)
    reconcile.ensure_schema(db)
    assert reconcile.preview({**PROFILE,'tenant_id':B,'telegram_id':22})['total']==0
