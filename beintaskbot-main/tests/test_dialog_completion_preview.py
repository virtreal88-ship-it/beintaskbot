"""Synthetic, read-only preparation of existing lifecycle completion actions."""
import asyncio
import json
import uuid
from unittest.mock import Mock

import pytest
from aiohttp.test_utils import make_mocked_request
from test_operator_list_performance import (fixture, operator_db, database, db,
    PROFILE, A, B, CA, CB, add_rows, measured)
import tenant_operator_dialogs as dialogs
import tenant_dialog_lifecycle as lifecycle
from tenant_dialog_rollout import transition
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def active(fixture, monkeypatch):
    import tenant_platform
    monkeypatch.setattr(tenant_platform,'_connect',lambda:fixture)
    with fixture.cursor() as cur:
        lifecycle.migrate(cur)
        transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    fixture.commit()
    return fixture


def message(d, n, *, tenant=A, channel=CA, lead=41, direction='incoming'):
    mid=str(uuid.UUID(int=800000+n))
    d.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,
      channel,direction,body,created_at) VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,
      'Synthetic','2099-01-01T00:00:00Z')''',
      (mid,tenant,lead,'preview-'+str(n),'connect:'+channel,direction))
    d.commit()
    return mid


def test_preview_exact_inbound_token_and_no_read_or_lifecycle_writes(active, monkeypatch):
    older=message(active,1)
    latest=message(active,2)
    message(active,3,channel=CB)
    message(active,4,tenant=B,channel=CB)
    message(active,5,direction='outgoing')
    tables=['saas_operator_dialog_reads','saas_dialog_workflows','saas_dialog_episodes',
            'saas_dialog_lifecycle_receipts','saas_tenant_audit_events']
    before={table:active.execute('SELECT count(*) AS n FROM '+table)[0]['n'] for table in tables}
    monkeypatch.setattr(dialogs,'_unread_counts',Mock(side_effect=AssertionError('preview must not touch read cursors')))
    result=dialogs.listing(PROFILE,completion_preview=True)
    assert result['completion_preview'] is True and result['unread_count'] is None
    row=next(r for r in result['dialogs'] if r['lead_id']==41)
    assert row['lifecycle']['last_inbound_id']==latest
    assert row['lifecycle']['can_dismiss'] is True
    assert row['lifecycle']['revision']==0
    after={table:active.execute('SELECT count(*) AS n FROM '+table)[0]['n'] for table in tables}
    assert after==before
    detail=dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']
    assert row['lifecycle']=={k:v for k,v in detail['lifecycle'].items() if k!='completed_episodes'}


def test_preview_has_constant_query_round_trips_and_default_is_unchanged(active):
    message(active,6)
    ordinary=dialogs.listing(PROFILE)
    assert 'completion_preview' not in ordinary
    assert ordinary['dialogs'][0]['lifecycle']['last_inbound_id'] is None
    small,small_sql=measured(active,dialogs.listing,completion_preview=True)
    add_rows(active,80)
    large,large_sql=measured(active,dialogs.listing,completion_preview=True)
    assert len(small_sql)==len(large_sql)
    assert len(large['dialogs'])==50 and large['next_offset']==50
    assert sum('WITH routes(channel_id,conversation_id,lead_id)' in q for q in large_sql)==1
    second=dialogs.listing(PROFILE,completion_preview=True,offset=50)
    assert second['completion_preview'] is True and second['next_offset'] is None


def test_new_inbound_after_preview_is_not_dismissed(active):
    prior=message(active,7)
    row=next(r for r in dialogs.listing(PROFILE,completion_preview=True)['dialogs'] if r['lead_id']==41)
    message(active,8)
    with pytest.raises(TenantLinearError) as denied:
        lifecycle.mutate(PROFILE,dict(provider='connect',channel_id=CA,conversation_id='client',
            lead_id=41,action='dismiss',expected_revision=row['lifecycle']['revision'],
            message_id=prior,request_id=str(uuid.uuid4())))
    assert denied.value.status==409
    assert active.execute('SELECT count(*) AS n FROM saas_dialog_lifecycle_receipts')[0]['n']==0


def test_preview_api_is_opt_in_and_identity_bound(monkeypatch):
    import tenant_operator_api as api
    read=Mock(return_value={'completion_preview':True,'dialogs':[]})
    monkeypatch.setattr(api,'listing',read)
    handler=api.handler(lambda request:PROFILE,'https://fixture.test',Mock(),'dialogs')
    response=asyncio.run(handler(make_mocked_request('GET','/api/platform/ai/dialogs?completion_preview=1')))
    assert response.status==200 and read.call_args.kwargs=={'completion_preview':True}
    read.reset_mock()
    response=asyncio.run(handler(make_mocked_request('GET','/api/platform/ai/dialogs')))
    assert response.status==200 and read.call_args.kwargs=={}
    read.reset_mock()
    response=asyncio.run(handler(make_mocked_request('GET','/api/platform/ai/dialogs?completion_preview=1',
        headers={'X-Tenant-Id':B,'X-User-Id':'11'})))
    assert response.status==409
    read.assert_not_called()


def test_preview_respects_disabled_admission(active):
    message(active,9)
    with active.cursor() as cur:
        transition(cur,A,mode='active',admission_enabled=False,expected_generation=1)
    active.commit()
    row=next(r for r in dialogs.listing(PROFILE,completion_preview=True)['dialogs'] if r['lead_id']==41)
    assert row['lifecycle']['last_inbound_id']
    assert row['lifecycle']['can_dismiss'] is False
