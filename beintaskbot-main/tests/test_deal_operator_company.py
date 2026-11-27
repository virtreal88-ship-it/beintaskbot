from contextlib import nullcontext
from unittest.mock import Mock

import pytest

import tenant_deal_operator_company as company
from tenant_beinsystems_operator import OperatorError
from tenant_crm_relation_contract import ProviderBinding
from test_tenant_support_inbox import A, B, database, db


TENANT = '11111111-1111-4111-8111-111111111111'
DEAL = '11111111-1111-4111-8111-111111111112'
LINK = '11111111-1111-4111-8111-111111111113'
INSTANCE = '11111111-1111-4111-8111-111111111114'
PROFILE = {'tenant_id': TENANT, 'telegram_id': 10, 'role': 'owner', 'active': True,
           'tenant_status': 'active'}
CREDENTIALS = {'generation': 'operator-1', 'connection_instance_id': INSTANCE,
               'token': 'test', 'base_url': 'https://test.invalid'}


@pytest.fixture
def access(monkeypatch):
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', TENANT)
    monkeypatch.setenv('BEINSYSTEMS_DIALOG_OPERATOR_CHOOSER_ENABLED', 'true')
    monkeypatch.setattr(company.operator, '_current_profile', lambda *_: PROFILE)


def test_source_keys_are_canonical_and_independent():
    assert company.target('native', DEAL) == ('native', DEAL)
    assert company.target('kommo', '78709721') == ('kommo', '78709721')
    for source, ident in [('connect', '78709721'), ('kommo', '01'), ('kommo', 1), ('native', '78709721')]:
        with pytest.raises(OperatorError):
            company.target(source, ident)


def test_native_view_grant_does_not_grant_binding_writes(access):
    cur = Mock()
    cur.fetchone.return_value = {'can_manage': False}
    _, _, _, scope, can_manage = company._deal_access(cur, PROFILE, ('native', DEAL))
    assert not can_manage and scope == ''
    assert 'FOR SHARE OF d' in cur.execute.call_args.args[0]
    assert cur.execute.call_args.args[1] == [TENANT, TENANT, DEAL, TENANT]
    cur.fetchone.return_value = None
    with pytest.raises(OperatorError) as error:
        company._deal_access(cur, PROFILE, ('native', DEAL))
    assert error.value.status == 404


def test_kommo_binding_is_scoped_to_proven_connection(access, monkeypatch):
    raw = {'responsible_user_id': 10}
    cur = Mock()
    cur.fetchone.return_value = {'tenant_id': TENANT, 'pipeline_id': 1, 'status_id': 2, 'raw': raw}
    bound = Mock(return_value=ProviderBinding(TENANT, 'kommo', 'test.kommo.com', 'oauth:v1'))
    proof = Mock(return_value={'99': 'verified'})
    monkeypatch.setattr(company, 'current_binding', bound)
    monkeypatch.setattr(company, 'revisions', proof)
    first = company._deal_access(cur, PROFILE, ('kommo', '99'))[3]
    proof.assert_called_with(cur, TENANT, 'leads', ['99'], bound.return_value, expected_raw={'99': raw})
    bound.return_value = ProviderBinding(TENANT, 'kommo', 'test.kommo.com', 'oauth:v2')
    assert company._deal_access(cur, PROFILE, ('kommo', '99'))[3] != first
    proof.return_value = {}
    with pytest.raises(OperatorError):
        company._deal_access(cur, PROFILE, ('kommo', '99'))


def test_read_revalidates_the_exact_deal_after_provider_response(monkeypatch):
    snapshot = (TENANT, 'account-1', 'operator-1', INSTANCE)
    snapshots = Mock(side_effect=[snapshot, snapshot, (TENANT, 'account-2', 'operator-1', INSTANCE)])
    reader = Mock(return_value={'balance': 91})
    monkeypatch.setattr(company, '_snapshot', snapshots)
    monkeypatch.setattr(company.operator, '_credentials', Mock(return_value=CREDENTIALS))
    monkeypatch.setattr(company.operator, '_record_operator_read', Mock())
    monkeypatch.setattr(company, 'profile_fields', reader)
    with pytest.raises(OperatorError) as error:
        company.read(PROFILE, ('native', DEAL), LINK, 'company_profile')
    assert error.value.status == 409
    reader.assert_called_once_with('account-1', token='test', base_url='https://test.invalid')
    assert snapshots.call_count == 3


def test_search_and_save_require_fresh_write_authority_and_binding(monkeypatch):
    conn, cur = Mock(), Mock()
    conn.cursor.side_effect = lambda: nullcontext(cur)
    monkeypatch.setattr(company.operator, '_connect', lambda: nullcontext(conn))
    deal_access = Mock(return_value=(TENANT, 10, object(), 'scope', True))
    monkeypatch.setattr(company, '_deal_access', deal_access)
    row = Mock(return_value=None)
    saved = Mock(return_value={'id': LINK, 'account': 'account-1'})
    monkeypatch.setattr(company, '_row', row)
    monkeypatch.setattr(company, '_save', saved)
    monkeypatch.setattr(company.chooser, '_assert_fresh', Mock())

    def search(profile, route, term, *, authorize, **kwargs):
        authorize(cur, profile, route)
        return TENANT, 10, 'active', CREDENTIALS, [term]

    def fence(cur, profile, route, *args, authorize):
        authorize(cur, profile, route)

    monkeypatch.setattr(company.chooser, '_read_live', search)
    monkeypatch.setattr(company.chooser, '_fenced_local', fence)
    route = ('native', DEAL)
    assert company.select(PROFILE, route, term='account-1')['accounts'] == [{'account': 'account-1'}]
    saved.assert_not_called()
    result = company.select(PROFILE, route, term='account-1', choice=True)
    assert result['binding']['company_id'] == LINK
    conn.commit.assert_called_once()
    row.return_value = {'id': LINK, 'account': 'account-1'}
    with pytest.raises(OperatorError) as error:
        company.select(PROFILE, route, term='account-2', choice=True, expected_company_id=None)
    assert error.value.status == 409
    deal_access.return_value = (TENANT, 10, object(), 'scope', False)
    with pytest.raises(OperatorError) as error:
        company.select(PROFILE, route, term='account-2')
    assert error.value.status == 403
    assert saved.call_count == 1


def test_disposable_postgres_binding_persists_and_unlinks_by_exact_deal(db, monkeypatch):
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', A)
    monkeypatch.setenv('BEINSYSTEMS_DIALOG_OPERATOR_CHOOSER_ENABLED', 'true')
    monkeypatch.setattr(company.operator, '_connect', lambda: db)
    db.execute("""INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name)
        VALUES(%s::uuid,'pipeline','Sales')""", (A,))
    db.execute("""INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name)
        VALUES(%s::uuid,'pipeline','new','New')""", (A,))
    second = '11111111-1111-4111-8111-111111111115'
    for ident in (DEAL, second):
        db.execute("""INSERT INTO saas_crm_native_deals(id,tenant_id,title,pipeline_id,stage_id)
            VALUES(%s::uuid,%s::uuid,'Synthetic','pipeline','new')""", (ident, A))
    db.commit()
    route = ('native', DEAL)
    profile = {'tenant_id': A, 'telegram_id': 11}
    assert company.binding(profile, route)['company_id'] is None
    with db.cursor() as cur:
        saved = company._save(cur, A, 11, route, '', None, 'verified-account', 'operator-1', INSTANCE)
        assert company._save(cur, A, 11, route, '', saved, 'verified-account', 'operator-1', INSTANCE) == saved
        with pytest.raises(OperatorError):
            company._save(cur, A, 11, route, '', None, 'other-account', 'operator-1', INSTANCE)
    db.commit()
    assert company.binding(profile, route)['company_name'] == 'verified-account'
    assert company.binding(profile, ('native', second))['company_id'] is None
    with pytest.raises(OperatorError):
        company.binding({'tenant_id': B, 'telegram_id': 22}, route)
    assert company.unlink(profile, route, saved['id'])['company_id'] is None
    assert company.binding(profile, route)['company_id'] is None
