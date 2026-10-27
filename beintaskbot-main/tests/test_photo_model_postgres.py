"""The model-change recovery remains one-use under native PostgreSQL races."""
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A
from test_single_photo_retry import prior
from test_photo_model import recovery
from test_single_photo_retry_postgres import race
import tenant_ai_single_message_extraction as consent
from test_photo_company_settings import company,configure


def test_model_recovery_duplicate_owner_completion_and_worker_claim(recovery):
    database,cfg,args,fp,_=recovery
    before=database.execute('SELECT id,state,reserved_microusd FROM saas_ai_spend_reservations ORDER BY id')
    prepared=consent.prepare(database.cursor(),cfg,args);database.commit()
    meta=prepared['_component_meta']['owner_single_extraction']
    payload={'consent_id':prepared['consent_id'],'capability':meta['capabilities']['approve'],
      'action':'approve','acknowledge_prior_unknown':True}
    results=race(database.dsn,'approve',cfg,payload)
    assert len(set(results))==1
    row=database.execute('SELECT campaign_id,fragment_id FROM saas_ai_single_message_extraction_consents WHERE id=%s::uuid',(prepared['consent_id'],))[0]
    database.commit()
    claims=race(database.dsn,'claim',cfg,{'campaign_id':str(row['campaign_id'])})
    assert sorted(claims,key=str)==sorted([str(row['fragment_id']),None],key=str)
    assert database.execute("SELECT count(*) AS n FROM saas_knowledge_campaign_fragments WHERE route ? 'photo_model'")[0]['n']==1
    assert database.execute('SELECT id,state,reserved_microusd FROM saas_ai_spend_reservations ORDER BY id')==before


def test_supported_company_setting_then_concurrent_owner_recovery(company):
    database=company[0]
    assert configure(database)['photo_model']=='gpt-4.1'
    test_model_recovery_duplicate_owner_completion_and_worker_claim(company)
