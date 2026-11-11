"""Unused scoped D: isolated database and fail-fast provider fixtures."""
import json
import uuid
import pytest
from test_ai_scoped_documents import scoped, grants, ACTOR, db, database, A
from tenant_ai_unused_archive import archive_unused_agent, install_schema_references
from tenant_linear_policy import TenantLinearError
import tenant_ai_agents_store as store


@pytest.mark.parametrize('scope,permitted',[('self',{'self'}),('department',{'self','peer'}),('all',{'self','peer','other','system'}),('none',set())])
def test_independent_delete_scope(scoped,scope,permitted):
    db,ids=scoped;grants(db,dict(view='none',create='none',update='none',delete=scope),manage=False)
    for name in ('self','peer','other','system','foreign'):
        if name in permitted:
            assert archive_unused_agent(ACTOR,ids[name],expected_version=1)=={'id':ids[name],'version':2,'archived':True}
            assert db.execute('SELECT id FROM saas_ai_agent_history_identities WHERE tenant_id=%s AND id=%s',(A,ids[name]))
        else:
            with pytest.raises(TenantLinearError) as error:archive_unused_agent(ACTOR,ids[name],expected_version=1)
            assert error.value.status==404
    with pytest.raises(TenantLinearError):store.list_agents(ACTOR)
    with pytest.raises(TenantLinearError):store.configuration_options(ACTOR)


def test_exact_version_revocation_and_legacy_boundary(scoped,monkeypatch):
    db,ids=scoped;grants(db,{'delete':'self'})
    for version in (None,True,'1',0):
        with pytest.raises(TenantLinearError) as error:archive_unused_agent(ACTOR,ids['self'],expected_version=version)
        assert error.value.status==400
    with pytest.raises(TenantLinearError) as error:archive_unused_agent(ACTOR,ids['self'],expected_version=2)
    assert error.value.status==409
    grants(db,{'delete':'none'})
    with pytest.raises(TenantLinearError):archive_unused_agent(ACTOR,ids['self'],expected_version=1)
    grants(db,{'delete':'self'})
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
    with pytest.raises(TenantLinearError):archive_unused_agent(ACTOR,ids['self'],expected_version=1)
    assert archive_unused_agent({'tenant_id':A,'telegram_id':11},ids['self'],expected_version=1)['archived']


@pytest.mark.parametrize('field,value',[('enabled',True),('scopes',[{'provider':'connect'}]),('expert_policy',{'allowed_primary_ids':['peer']})])
def test_outgoing_binding_blocks_without_changes(scoped,field,value):
    db,ids=scoped;grants(db,{'delete':'all'})
    if field=='expert_policy':value={'allowed_primary_ids':[ids['peer']]}
    db.execute(f'UPDATE saas_ai_agents SET {field}=%s WHERE id=%s',(value if field=='enabled' else json.dumps(value),ids['self']));db.commit()
    with pytest.raises(TenantLinearError) as error:archive_unused_agent(ACTOR,ids['self'],expected_version=1)
    assert error.value.status==409
    assert db.execute('SELECT archived_at,version FROM saas_ai_agents WHERE id=%s',(ids['self'],))[0]=={'archived_at':None,'version':1}


def test_trigger_and_reverse_team_block(scoped):
    db,ids=scoped;grants(db,{'delete':'self'})
    db.execute('UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE id=%s',(json.dumps({'allowed_primary_ids':[ids['self']]}),ids['peer']));db.commit()
    with pytest.raises(TenantLinearError) as error:archive_unused_agent(ACTOR,ids['self'],expected_version=1)
    assert str(error.value)=='ai_agent_archive_requires_unused_agent'
    db.execute("UPDATE saas_ai_agents SET expert_policy='{}' WHERE id=%s",(ids['peer'],))
    db.execute("INSERT INTO saas_ai_agent_triggers(tenant_id,id,agent_id,name,trigger_type,actions) VALUES(%s,%s,%s,'trigger','phrase','[]')",(A,str(uuid.uuid4()),ids['self']));db.commit()
    with pytest.raises(TenantLinearError):archive_unused_agent(ACTOR,ids['self'],expected_version=1)


def test_new_structural_references_rejected_but_prose_not_a_reference(scoped):
    db,ids=scoped;grants(db,{'delete':'self'})
    archive_unused_agent(ACTOR,ids['self'],expected_version=1)
    with pytest.raises(Exception,match='ai_agent_archived_reference'):
        db.execute('UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE id=%s',(json.dumps({'allowed_primary_ids':[ids['self']]}),ids['peer']))
    db.rollback()
    db.execute('UPDATE saas_ai_agents SET instructions=%s,expert_policy=%s::jsonb WHERE id=%s',(ids['self'],json.dumps({'notes':ids['self']}),ids['peer']));db.commit()
    assert db.execute('SELECT instructions FROM saas_ai_agents WHERE id=%s',(ids['peer'],))[0]['instructions']==ids['self']


def test_optional_table_installed_after_archive_is_fenced(scoped,monkeypatch):
    db,ids=scoped;grants(db,{'delete':'self'})
    archive_unused_agent(ACTOR,ids['self'],expected_version=1)
    import tenant_ai_simulation_schema as simulation_schema
    # This test has a fresh module-scoped DB, but a prior module may have set
    # the simulation bootstrap's process-local cache for a different DB.
    # Explicitly run its real bootstrap here, restoring the prior flag afterward.
    monkeypatch.setattr(simulation_schema,'_ready',False)
    with store._connect() as conn:simulation_schema.ensure_schema(conn);conn.commit()
    assert db.execute("SELECT to_regclass('saas_ai_simulation_configs') AS present")[0]['present']
    assert db.execute("SELECT 1 FROM pg_trigger WHERE tgrelid='saas_ai_simulation_configs'::regclass AND tgname='saas_ai_unused_archive_reference_v1' AND tgenabled IN ('O','A')")
    with pytest.raises(Exception,match='ai_agent_archived_reference'):
        db.execute('INSERT INTO saas_ai_simulation_configs(tenant_id,agent_id) VALUES(%s,%s)',(A,ids['self']))
    db.rollback()


def test_current_projection_has_independent_delete(scoped):
    db,ids=scoped;grants(db,{'view':'all','update':'none','delete':'self'})
    rows=store.list_agents(ACTOR)
    assert {row['name'] for row in rows if row['capabilities']['delete']}=={'self'}
    assert not any(row['capabilities']['update'] for row in rows)


def test_reference_extractor_uses_relationship_keys_not_prose(scoped):
    db,ids=scoped
    data={'notes':ids['self'],'agent_assignment_id':ids['self'],'nested':{'allowed_primary_ids':[ids['peer']]},'developer_reply_bindings':[{'developer_expected_agent_id':ids['other']}]}
    result=db.execute('SELECT saas_ai_reference_ids(%s::jsonb) AS ids',(json.dumps(data),))[0]['ids']
    assert set(map(str,result))=={ids['peer'],ids['other']}


def test_installed_reference_manifest_has_no_missing_columns_or_fences(scoped):
    db,_=scoped
    from tenant_ai_unused_archive import REFERENCES
    for table,columns in REFERENCES.items():
        if not db.execute('SELECT to_regclass(%s) AS present',(table,))[0]['present']:continue
        found={r['attname'] for r in db.execute('SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped',(table,))}
        assert set(columns)<=found,(table,set(columns)-found)
        assert db.execute("SELECT 1 FROM pg_trigger WHERE tgrelid=%s::regclass AND tgname='saas_ai_unused_archive_reference_v1' AND tgenabled<>'D'",(table,))


def test_optional_schema_creators_install_their_actual_reference_fences(scoped,monkeypatch):
    import importlib
    db,_=scoped
    creators=[('tenant_ai_instructor_store','schema'),('tenant_ai_simulation_schema','ensure_schema'),
              ('tenant_dialog_consultations','schema'),('tenant_dialog_audio_store','schema'),
              ('tenant_owner_asr_comparison','schema'),('tenant_instruction_library','schema'),('tenant_instruction_videos','schema'),
              ('tenant_ai_learning_mcp','ensure_mcp_schema')]
    for module,name in creators:
        implementation=importlib.import_module(module)
        if module=='tenant_ai_simulation_schema':monkeypatch.setattr(implementation,'_ready',False)
        with store._connect() as conn:
            getattr(implementation,name)(conn);conn.commit()
    test_installed_reference_manifest_has_no_missing_columns_or_fences(scoped)


def test_archive_api_checks_identity_origin_and_returns_only_receipt(scoped):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock,AsyncMock
    from tenant_ai_agents_api import handler
    db,ids=scoped;grants(db,{'view':'none','delete':'self'},manage=False)
    remote=AsyncMock(side_effect=AssertionError('provider forbidden'))
    call=handler(lambda request:ACTOR,'https://synthetic.invalid',Mock(),'agent_archive_unused',request_provider=remote)
    def request(origin='https://synthetic.invalid',actor=31):
        return SimpleNamespace(method='POST',content_length=200,headers={'Origin':origin},content_type='application/json',match_info={'agent_id':ids['self']},json=AsyncMock(return_value={'expected_tenant_id':A,'expected_user_id':actor,'expected_version':1}))
    assert asyncio.run(call(request(origin='https://other.invalid'))).status==403
    assert asyncio.run(call(request(actor=32))).status==409
    response=asyncio.run(call(request()))
    assert response.status==200,response.text
    assert json.loads(response.text)['result']=={'id':ids['self'],'version':2,'archived':True}
    remote.assert_not_called()


def test_missing_disabled_or_stale_fence_is_not_schema_ready(scoped):
    db,ids=scoped
    from tenant_ai_unused_archive import fences_ready,install_existing
    for change in ('ALTER TABLE saas_ai_agents DISABLE TRIGGER saas_ai_unused_archive_reference_v1',
                   'DROP TRIGGER saas_ai_unused_archive_reference_v1 ON saas_ai_agents'):
        db.execute(change);db.commit()
        with db.cursor() as cur:
            assert not fences_ready(cur)
            install_existing(cur)
            assert fences_ready(cur)
        db.commit()
    db.execute('DROP TRIGGER saas_ai_unused_archive_reference_v1 ON saas_ai_agents')
    db.execute("CREATE TRIGGER saas_ai_unused_archive_reference_v1 BEFORE INSERT OR UPDATE ON saas_ai_agents FOR EACH ROW EXECUTE FUNCTION saas_ai_unused_archive_reference_fn('name')")
    db.commit()
    with db.cursor() as cur:
        assert not fences_ready(cur)
        install_existing(cur)
        assert fences_ready(cur)
    db.commit()


@pytest.mark.parametrize('state',['queued','processing','drafted','skipped'])
def test_any_job_including_active_or_historical_blocks_archive(scoped,state):
    db,ids=scoped;grants(db,{'delete':'self'})
    db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider) VALUES(%s,'unused-job-test','connect')",(A,))
    db.execute("""INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,conversation_id,lead_id,talk_id,chat_id,event_fingerprint,state,due_at)
      VALUES(%s,%s,'connect','synthetic',1,'','','unused-job-test',%s,now())""",(A,ids['self'],state));db.commit()
    with pytest.raises(TenantLinearError) as error:archive_unused_agent(ACTOR,ids['self'],expected_version=1)
    assert error.value.status==409
    assert db.execute('SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s AND agent_id=%s',(A,ids['self']))[0]['state']==state


def test_pending_webhook_nested_binding_blocks_archive(scoped):
    db,ids=scoped;grants(db,{'delete':'self'})
    db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,payload) VALUES(%s,'unused-event-test','connect',%s::jsonb)",(A,json.dumps({'developer_reply_bindings':[{'developer_expected_agent_id':ids['self']}]})));db.commit()
    with pytest.raises(TenantLinearError) as error:archive_unused_agent(ACTOR,ids['self'],expected_version=1)
    assert error.value.status==409


def test_gate_off_owner_archive_and_deleted_tombstone_keep_legacy_history(scoped,monkeypatch):
    db,ids=scoped;monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
    owner={'tenant_id':A,'telegram_id':11}
    result=store.delete_agent(owner,ids['self'],replacement_agent_id=ids['peer'],expected_version=1)
    assert result['archived']
    assert not db.execute('SELECT id FROM saas_ai_scoped_archives WHERE id=%s',(ids['self'],))
    db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider) VALUES(%s,'legacy-history-test','connect')",(A,))
    def insert_run(revision):
        db.execute("""INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state)
          VALUES(%s,%s,%s,'legacy-history-test',%s,'fixture','completed')""",(A,str(uuid.uuid4()),ids['self'],revision));db.commit()
    insert_run(1)
    result=store.permanently_delete_agent(owner,ids['self'],expected_version=2,confirm_agent_id=ids['self'])
    assert result['deleted']
    insert_run(2)
    assert db.execute('SELECT count(*) AS n FROM saas_ai_agent_runs WHERE tenant_id=%s AND agent_id=%s',(A,ids['self']))[0]['n']==2
    assert db.execute('SELECT deleted_at FROM saas_ai_agent_history_identities WHERE id=%s',(ids['self'],))[0]['deleted_at']
