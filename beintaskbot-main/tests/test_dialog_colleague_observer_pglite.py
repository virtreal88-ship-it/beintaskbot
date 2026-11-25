"""Foundation for colleague observation; all identities and messages are synthetic."""
import json

import pytest

from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route
from test_dialog_lifecycle_pglite import command, episodes
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, database, db


@pytest.mark.parametrize("next_operator", [11, 55])
def test_observer_proof_follows_workflow_assignee_and_source_grants(episodes, monkeypatch, next_operator):
    db = episodes
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    for employee, name in ((44, 'Synthetic colleague B'), (55, 'Synthetic colleague C')):
        db.execute("""INSERT INTO saas_tenant_members
          (tenant_id,telegram_id,role,display_name,permissions)
          VALUES(%s::uuid,%s,'operator',%s,'["dialogs"]'::jsonb)""", (A,employee,name))
    config = {str(employee):{'access_scopes':{'dialogs':{'view':'self','update':'self'}}}
              for employee in (33,44,55)}
    db.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
      VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT (tenant_id,policy_key)
      DO UPDATE SET value=EXCLUDED.value""",(A,json.dumps(config)))
    for employee in (33,44,55):
        db.execute("""INSERT INTO saas_dialog_source_members
          (tenant_id,provider,source_id,employee_id,updated_by)
          VALUES(%s::uuid,'connect',%s::uuid,%s,11)""",(A,CA,employee))
    db.commit()

    from tenant_dialog_presence import set_status
    for employee in (44,55):
        set_status({'tenant_id':A,'telegram_id':employee},{'status':'online'})
    assert command()['lifecycle']['operator_id']==11
    assert command('transfer',1,target_employee_id=44)['lifecycle']['operator_id']==44

    staff={'tenant_id':A,'telegram_id':33}

    def open_route(target, *, profile=staff, provider='connect', source=CA, lead=41, action='view'):
        with db, db.cursor() as cur:
            tenant, _, policy=access(cur,profile,'dialogs')
            return assert_route(cur,tenant,policy,provider,'client',source,lead,
                                action=action,observed_operator_id=target)

    assert open_route(44)['lead_id']==41
    from tenant_operator_dialogs import history, listing, colleague_options
    import tenant_chat_attachment_store as attachments
    monkeypatch.setattr('tenant_operator_dialogs._connect',lambda: db)

    external_id='connect:00000000-0000-4000-8000-000000000701'
    db.execute("""INSERT INTO saas_crm_messages
      (id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,raw)
      VALUES(gen_random_uuid(),%s::uuid,41,%s,%s,'incoming','','image','{}'::jsonb)""",(A,external_id,'connect:'+CA))
    db.commit()
    monkeypatch.setattr(attachments,'_connect',lambda: db)
    monkeypatch.setattr('tenant_connect_transport.connect_route',
                       lambda cur,tenant,lead,historical_read: {'channel_id':CA,'conversation_id':'client','api_key':'not-a-real-key'})

    def media(target, source=CA):
        return attachments.load(staff,41,external_id,channel_id=source,conversation_id='client',
                                observed_operator_id=target)

    assert media(44)['provider']=='connect'
    with pytest.raises(TenantLinearError):
        media(44,CB)

    def read(target, **kwargs):
        return history(staff,'connect',41,CA,'client',observed_operator_id=target,**kwargs)

    assert [x['id'] for x in colleague_options(staff)['operators']]==['44','55']
    def colleague_list(target, **kwargs):
        return listing(staff,observed_operator_id=target,**kwargs)['dialogs']

    assert [x['lead_id'] for x in colleague_list(44)]==[41]
    assert colleague_list(55)==[]
    assert colleague_list(44,lifecycle='completed')==[]
    assert colleague_list(44,channel='connect:'+CB)==[]
    assert colleague_list(44,search='no match')==[]
    view=read(44)
    assert view['dialog']['colleague_read_only'] is True
    assert view['dialog']['can_send'] is False
    assert view['dialog']['browser_call_available'] is False
    assert all(not value for key,value in view['dialog']['lifecycle'].items() if key.startswith('can_'))
    assert view['messages']
    assert view['read_through_message_id'] is None
    assert 'shared_reminder' not in view['dialog']
    assert 'company_binding' not in view['dialog']
    for mode in ('owner_overview','search_overview','support_case_only','combine_history',
                 'include_operator_events','include_shared_reminder_events'):
        with pytest.raises(TenantLinearError):
            read(44,**{mode:True})
    for target, options in ((None,{}),(33,{}),(11,{}),(55,{}),(22,{}),(44,{'lead':None}),
                            (44,{'source':CB}),(44,{'provider':'kommo'}),(44,{'action':'update'}),
                            (44,{'profile':PROFILE})):
        with pytest.raises(TenantLinearError):
            open_route(target,**options)

    # Membership checks are repeated, not inherited from the dropdown.
    db.execute("""DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid
      AND source_id=%s::uuid AND employee_id=33""",(A,CA))
    db.commit()
    with pytest.raises(TenantLinearError):
        open_route(44)
    db.execute("""INSERT INTO saas_dialog_source_members
      (tenant_id,provider,source_id,employee_id,updated_by)
      VALUES(%s::uuid,'connect',%s::uuid,33,11)""",(A,CA))
    db.commit()

    assert command('transfer',2,actor=44,target_employee_id=next_operator)['lifecycle']['operator_id']==next_operator
    with pytest.raises(TenantLinearError):
        open_route(44)
    with pytest.raises(TenantLinearError):
        read(44)
    assert colleague_list(44)==[]
    with pytest.raises(TenantLinearError):
        media(44)
    if next_operator==55:
        assert open_route(55)['lead_id']==41
    else:
        with pytest.raises(TenantLinearError):
            open_route(11)
