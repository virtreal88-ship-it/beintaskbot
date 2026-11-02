"""LANG01-04 acceptance through native SQL, real policy/authority and consumers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import logging
import re
import threading
import uuid
from unittest.mock import AsyncMock, Mock

from cryptography.fernet import Fernet
import psycopg
from psycopg.rows import dict_row
import pytest

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, B, CA, CB, AGENT, PROFILE, seed_job
import tenant_company_admin as company_admin
import tenant_company_admin_api as company_api
import tenant_dialog_consultations as consultations
import tenant_support_inbox_store as cases
import tenant_support_inbox_summary as summaries
import tenant_summary_language as languages
import tenant_knowledge_candidates_store as candidate_store
import tenant_knowledge_candidates_service as candidates
import tenant_knowledge_improve as improve
import tenant_ai_agent_run_store as runs
import tenant_ai_spend_budget as spending
import tenant_night_report_store as night_report
import tenant_media_evidence as evidence
import tenant_episode_media as episode
import tenant_chat_ai_provider as provider
import tenant_connect_transport as transport
import tenant_connect_media as connect_media
import tenant_knowledge_media as knowledge_media
import tenant_ai_group_coordination as groups
import tenant_workflow_store as workflow
import tenant_member_store as members
from tenant_platform import TenantPlatformError
from tenant_linear_policy import TenantLinearError


LOGGER = logging.getLogger(__name__)
DEVELOPER = 'synthetic-summary-developer'
AZ = 'Salam, proqramda satis acilmir ve mehsul gorunmur. Zehmet olmasa bu meseleni hell edin.'
RU = 'Здравствуйте, программа не открывает окно продажи и не показывает товар. Помогите исправить эту проблему.'
EN = 'Hello, the program cannot open the sales screen and the product is not showing. Please help fix this issue.'
TR = 'Merhaba, programda faturayi kaydedemiyorum ve urun bilgisi gorunmuyor. Lutfen yardim eder misiniz?'
SUMMARY = {
    'az': 'Müştəri proqramda satış ekranını aça bilmir.',
    'ru': 'Клиент не может открыть окно продаж.',
    'en': 'The customer cannot open the sales screen.',
    'tr': 'Müşteri satış ekranını açamıyor.',
}


def _policy(profile=PROFILE, *, mode='automatic', fixed=None, fallback='az'):
    current = languages.read(profile)
    return languages.save(profile, {'expected_version': current['version'], 'mode': mode,
        'fallback_language': fallback, 'fixed_language': fixed})


@pytest.fixture
def language_db(db, monkeypatch):
    seed_job(db)
    key = Fernet.generate_key()
    cipher = Fernet(key)
    monkeypatch.setenv('TENANT_ENCRYPTION_KEY', key.decode())
    for key_name in ('AI_AGENTS_PLATFORM_ENABLED', 'AI_AGENTS_ENABLED', 'AI_AGENTS_AUTO_SEND_ENABLED', 'AI_SIMULATION_ENABLED'):
        monkeypatch.setenv(key_name, 'false')
    for key_name in ('AI_AGENTS_PLATFORM_KEY_AGENTS', 'AI_AGENTS_PLATFORM_KEY_ACTIVATED_AT'):
        monkeypatch.setenv(key_name, '')
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD', '{}')
    factory = lambda: psycopg.connect(db.dsn, row_factory=dict_row)
    for module in (languages, cases, summaries, consultations, company_admin, candidate_store,
                   candidates, runs, spending, night_report, evidence, episode, knowledge_media):
        monkeypatch.setattr(module, '_connect', factory)
    for module in (workflow, members, groups):
        monkeypatch.setattr(module, '_connect', factory)
    db.execute("UPDATE saas_ai_agents SET model='summary-fixture-model',language='az',mode='manual',scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
        (json.dumps([{'provider': 'connect', 'channel_id': CA}]), A, AGENT))
    encrypted = cipher.encrypt(json.dumps({'tenant_id': A, 'provider': 'chat_ai', 'api_key': 'synthetic-no-live-key'}).encode())
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets,metadata) VALUES(%s::uuid,'chat_ai','connected',%s,%s::jsonb)",
        (A, encrypted, json.dumps({'enabled': True, 'consent': True, 'model': 'summary-fixture-model',
                                 'models': ['summary-fixture-model'], 'daily_limit': 100})))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,%s,99,'Synthetic developer')", (A, CA, DEVELOPER))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions) VALUES(%s::uuid,33,'operator','Synthetic employee','[\"dialogs\",\"support\"]'::jsonb)", (A,))
    db.execute('DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid', (A,))
    db.commit()
    consultations.developer_marker(PROFILE, {'channel_id': CA, 'conversation_id': DEVELOPER, 'enabled': True})
    state = {'db': db, 'factory': factory, 'cipher': cipher, 'output_language': 'az', 'generation': 0}

    def generated(*_args, **_kwargs):
        state['generation'] += 1
        value = SUMMARY[state['output_language']] + ' #' + str(state['generation'])
        return json.dumps({field: value for field in ('description', 'summary', 'suggestions', 'next_steps')}, ensure_ascii=False), {
            'input_tokens': 20, 'output_tokens': 5, 'total_tokens': 25}

    state['provider'] = Mock(side_effect=generated)
    monkeypatch.setattr(summaries, 'generate', state['provider'])
    state['forbidden_http'] = Mock(side_effect=AssertionError('Live provider/Connect transport is forbidden'))
    monkeypatch.setattr(provider.requests.sessions.Session, 'request', state['forbidden_http'])
    monkeypatch.setattr(transport, '_request', state['forbidden_http'])
    monkeypatch.setattr(provider, 'transcribe', state['forbidden_http'])
    monkeypatch.setattr(provider, 'transcribe_video', state['forbidden_http'])
    state['forbidden_download'] = AsyncMock(side_effect=AssertionError('Language detection must not fetch media'))
    monkeypatch.setattr(connect_media, 'download_media', state['forbidden_download'])
    monkeypatch.setattr(knowledge_media, 'download_media', state['forbidden_download'])
    yield state
    state['forbidden_http'].assert_not_called()
    state['forbidden_download'].assert_not_called()


def _messages(f, messages, *, tenant=A, channel=CA, lead=41):
    result = []
    for index, message in enumerate(messages):
        value = {'direction': 'incoming', 'kind': 'text', 'raw': {}, **({'body': message} if isinstance(message, str) else message)}
        ident = str(uuid.uuid4())
        f['db'].execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,
          direction,channel,body,message_type,raw,happened_at) VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s::jsonb,
          clock_timestamp()+(%s * interval '1 second'))""",
          (ident, tenant, lead, 'connect:' + ident, value['direction'], 'connect:' + channel,
           value.get('body', ''), value['kind'], json.dumps(value['raw']), index))
        if value.get('saved_transcript') or value.get('saved_receipt'):
            row = f['db'].execute('SELECT external_id,body,message_type,raw,channel FROM saas_crm_messages WHERE id=%s::uuid', (ident,))[0]
            ref = episode.reference(row, channel, lead)
            with f['db'].cursor() as cur:
                receipt = value.get('saved_receipt') or {'status': 'processed',
                    'code': 'transcribed', 'transcript': value['saved_transcript']}
                evidence.persist(cur, tenant, [ref], [{'id': ref['id'], **receipt}], processor='conversation_episode')
        if value.get('legacy_voice'):
            raw = dict(value['raw'])
            raw['voice_transcription'] = {'media_version': evidence.media_version(raw)}
            if value['legacy_voice'] == 'missing_version':
                raw['voice_transcription'] = {}
            if value['legacy_voice'] == 'replaced':
                raw['mediaId'] = 'replacement-media-version'
            f['db'].execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid', (json.dumps(raw), ident))
            f['db'].execute("""INSERT INTO saas_connect_voice_receipts
              (tenant_id,channel_id,external_id,run_id,model,state)
              VALUES(%s::uuid,%s::uuid,%s,%s::uuid,'synthetic-asr','completed')""",
              (tenant, channel, 'connect:' + ident, str(uuid.uuid4())))
        result.append(ident)
    f['db'].commit()
    return result


def _case(f, messages, *, ui='ru', expected='az', automatic=True, profile=None, lead=41,
          tenant=A, channel=CA, developer=DEVELOPER):
    profile = profile or {**PROFILE, 'language': ui, 'locale': ui, 'ui_language': ui}
    if automatic and languages.read(profile)['source'] == 'legacy':
        _policy(profile)
    ids = _messages(f, messages, tenant=tenant, channel=channel, lead=lead)
    f['output_language'] = expected
    result = cases.create(profile, {'request_id': str(uuid.uuid4()), 'lead_id': lead, 'message_ids': ids,
        'assigned_developer_channel_id': channel, 'assigned_developer_conversation_id': developer,
        'developer_confirmed': True})
    return result, ids


def _assert_language(f, result, language, *, method=None):
    ticket = result['ticket']
    selection = ticket['summary_language']
    assert selection['language'] == language
    assert selection['detector_version'] == languages.DETECTOR_VERSION
    if method:
        assert selection['method'] == method
    assert ticket['summary_state'] == 'completed' and SUMMARY[language] in ticket['summary']
    assert languages.LANGUAGES[language] in f['provider'].call_args.kwargs['instructions']
    return selection


def test_company_policy_real_native_routes_scope_rbac_and_optimistic_version(language_db):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    f = language_db

    async def scenario():
        actor = {**PROFILE, 'role': 'owner'}
        app = web.Application()
        app.router.add_route('*', '/api/platform/companies/manage', company_api.handler(lambda _: actor, 'https://crm.test', LOGGER))
        async with TestClient(TestServer(app)) as client:
            path = '/api/platform/companies/manage?resource=summary_language'
            response = await client.get(path)
            assert response.status == 200
            initial = (await response.json())['summary_language']
            assert initial['mode'] == 'legacy' and initial['version'] == 0
            payload = {'action': 'summary_language', 'expected_tenant_id': A, 'expected_user_id': 11,
                'expected_version': 0, 'mode': 'automatic', 'fallback_language': 'az', 'fixed_language': None}
            assert (await client.post(path, json=payload)).status == 403
            assert (await client.post(path, headers={'Origin': 'https://foreign.test'}, json=payload)).status == 403
            assert (await client.post(path, headers={'Origin': 'https://crm.test'}, json={**payload, 'expected_tenant_id': B})).status == 409
            response = await client.post(path, headers={'Origin': 'https://crm.test'}, json=payload)
            assert response.status == 200
            saved = (await response.json())['summary_language']
            assert saved['version'] == 1 and saved['mode'] == 'automatic'
            assert (await client.post(path, headers={'Origin': 'https://crm.test'}, json=payload)).status == 409
            assert (await client.get(path)).headers['Cache-Control'] == 'no-store'
            actor.update(telegram_id=33, role='owner')
            assert (await client.get(path)).status == 403
            assert (await client.post(path, headers={'Origin': 'https://crm.test'}, json={**payload, 'expected_user_id': 33, 'expected_version': 1})).status == 403
            actor.update(tenant_id=B, telegram_id=11)
            assert (await client.get(path)).status == 403
        assert languages.read(PROFILE)['version'] == 1
        assert languages.read({'tenant_id': B, 'telegram_id': 22})['source'] == 'legacy'
        assert not f['db'].execute('SELECT id FROM saas_ai_agent_runs')
        f['provider'].assert_not_called()

    asyncio.run(scenario())


def test_policy_native_concurrent_save_has_one_winner_and_no_ddl(language_db, monkeypatch):
    f = language_db

    class NoDdlCursor(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            assert not re.search(r'^\s*(CREATE|ALTER|DROP|DO|TRUNCATE)\b', str(query), re.I)
            return super().execute(query, params, **kwargs)

    monkeypatch.setattr(languages, '_connect', lambda: psycopg.connect(f['db'].dsn, row_factory=dict_row, cursor_factory=NoDdlCursor))
    gate = threading.Barrier(2)

    def save(code):
        gate.wait(5)
        try:
            return languages.save(PROFILE, {'expected_version': 0, 'mode': 'fixed',
                'fallback_language': 'az', 'fixed_language': code})
        except TenantLinearError as error:
            return {'status': error.status}

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, ['ru', 'en']))
    assert sum(row.get('version') == 1 for row in results) == 1
    assert [row['status'] for row in results if 'status' in row] == [409]
    assert languages.read(PROFILE)['version'] == 1
    f['provider'].assert_not_called()


@pytest.mark.parametrize('key', ['summary_language', ' summary_language ', '\tsummary_language\n'])
def test_existing_generic_workflow_cannot_bypass_summary_policy_version_with_alias(language_db, key):
    f = language_db
    _policy()
    stale = f['db'].execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='summary_language'", (A,))[0]['value']
    _policy(mode='fixed', fixed='ru')
    before = f['db'].execute("SELECT value,updated_at FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='summary_language'", (A,))[0]
    f['db'].commit()
    for value in (stale, {**stale, 'version': 999, 'mode': 'fixed', 'fixed_language': 'en'}):
        with pytest.raises(TenantPlatformError):
            workflow.save_tenant_workflow_config(tenant_id=A, owner_id=11, policies={key: value})
        assert f['db'].execute("SELECT value,updated_at FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='summary_language'", (A,))[0] == before
        f['db'].commit()
    assert languages.read(PROFILE)['version'] == 2
    assert not f['db'].execute("SELECT id FROM saas_tenant_audit_events WHERE action='workflow_updated'")
    # A current canonical echo from the existing editor is harmless and does
    # not increment or overwrite the dedicated language-policy version.
    workflow.save_tenant_workflow_config(tenant_id=A, owner_id=11, policies={'summary_language': before['value']})
    assert languages.read(PROFILE)['version'] == 2
    f['provider'].assert_not_called()


@pytest.mark.parametrize('ui,messages,expected', [
    ('ru', [AZ], 'az'), ('az', [RU], 'ru'),
    ('ru', [EN, {'body': RU * 5, 'raw': {'verified_employee_id': 33}, 'author_name': 'Developer'}], 'en'),
    ('en', [AZ, 'ok'], 'az'),
    ('ru', [AZ, AZ, 'Хорошо, спасибо за помощь.', 'Да, большое спасибо.'], 'az'),
    ('ru', [AZ, AZ, 'Спасибо за помощь вам.', 'Большое спасибо за вашу помощь.'], 'az'),
    ('ru', [AZ, 'Программа не сохраняет документ и показывает ошибку подключения.',
            'После перезагрузки эта ошибка повторяется, продажу завершить невозможно.'], 'ru'),
    ('ru', [EN, 'Please reply in Russian about this issue.'], 'ru'),
    ('ru', [AZ, 'SKU-42 https://example.invalid/item 12345 😃 ok'], 'az'),
    ('ru', [{'kind': 'voice', 'body': '', 'saved_transcript': AZ}], 'az'),
    ('ru', [TR], 'tr'),
    ('ru', [AZ, {'body': EN * 6, 'direction': 'outgoing'}, {'body': RU * 6, 'raw': {'verified_employee_id': 33}}], 'az'),
    ('az', [RU, EN, 'Please help, the invoice is not saving after this error.'], 'en'),
])
def test_actual_support_summary_uses_customer_evidence_not_ui_or_employee(language_db, ui, messages, expected):
    f = language_db
    result, _ids = _case(f, messages, ui=ui, expected=expected)
    selection = _assert_language(f, result, expected)
    assert selection['policy_version'] == 1 and selection['policy_source'] == 'company'
    if any(isinstance(row, dict) and row.get('saved_transcript') for row in messages):
        assert selection['method'] == 'customer_detected', 'Saved speech must participate; matching fallback alone is insufficient'
    f['provider'].assert_called_once()
    assert f['db'].execute('SELECT reserved_calls,state FROM saas_ai_agent_runs')[0] == {'reserved_calls': 1, 'state': 'completed'}


def test_actual_support_missing_media_uses_az_fallback_and_marks_incomplete(language_db):
    f = language_db
    result, _ids = _case(f, [{'kind': 'voice', 'body': ''}], expected='az')
    _assert_language(f, result, 'az', method='fallback')
    context = f['provider'].call_args.kwargs['extra_context']
    assert context.get('media_incomplete') is True or context.get('incomplete_media') is True
    assert any('not analyzed' in line['text'].lower() or 'unavailable' in line['text'].lower() for line in f['provider'].call_args.args[3])


@pytest.mark.parametrize('identified_client', [True, False])
def test_native_group_summary_uses_proven_customer_participants_and_excludes_registered_staff(language_db, identified_client):
    f = language_db
    group = '120363400000777@g.us'
    f['db'].execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,43,'Synthetic customer group','group')", (A, CA, group))
    f['db'].execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
        (json.dumps([{'provider': 'connect', 'channel_id': CA, 'allow_group_messages': True}]), A, AGENT))
    f['db'].execute("INSERT INTO saas_ai_group_staff(tenant_id,channel_id,jid,member_id,verified_by) VALUES(%s::uuid,%s::uuid,'15550001111@s.whatsapp.net',33,11)", (A, CA))
    f['db'].commit()
    groups.configure(PROFILE, {'conversation_id': group, 'expected_revision': 0,
        'assignments': [{'agent_id': AGENT, 'channel_id': CA, 'priority': 0}]})
    raw = {'participantJid': '15550009999@s.whatsapp.net'} if identified_client else {}
    expected = 'en' if identified_client else 'az'
    result, _ids = _case(f, [{'body': EN, 'raw': raw}, {'body': RU * 6,
        'raw': {'participantJid': '15550001111@s.whatsapp.net'}}], expected=expected, lead=43)
    _assert_language(f, result, expected, method='customer_detected' if identified_client else 'fallback')
    f['provider'].assert_called_once()


def test_selected_case_language_does_not_read_prior_unselected_dialog_history(language_db):
    f = language_db
    old = _messages(f, [RU * 20])
    result, selected = _case(f, [AZ], expected='az')
    _assert_language(f, result, 'az', method='customer_detected')
    assert {row['id'] for row in result['messages']} == set(selected)
    assert not set(old) & set(selected)
    assert RU not in str(f['provider'].call_args.args[3])


def test_unconfigured_other_company_keeps_actual_legacy_agent_summary_policy(language_db):
    f = language_db
    actor = {'tenant_id': B, 'telegram_id': 22, 'language': 'ru', 'locale': 'ru'}
    other_agent = str(uuid.UUID(int=302))
    f['db'].execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,mode,model,language,scopes) VALUES(%s::uuid,%s::uuid,'Legacy company agent',22,TRUE,'manual','summary-fixture-model','en',%s::jsonb)",
        (B, other_agent, json.dumps([{'provider': 'connect', 'channel_id': CB}])))
    f['db'].execute('INSERT INTO saas_ai_agent_settings(tenant_id,enabled) VALUES(%s::uuid,TRUE)', (B,))
    f['db'].execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state) VALUES(%s::uuid,'connect',%s,'client',%s::uuid,'active')", (B, CB, other_agent))
    encrypted = f['cipher'].encrypt(json.dumps({'tenant_id': B, 'provider': 'chat_ai', 'api_key': 'synthetic-other-company-key'}).encode())
    f['db'].execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets,metadata) VALUES(%s::uuid,'chat_ai','connected',%s,%s::jsonb)",
        (B, encrypted, json.dumps({'enabled': True, 'consent': True, 'model': 'summary-fixture-model', 'daily_limit': 50})))
    f['db'].execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,%s,98,'Other company developer')", (B, CB, DEVELOPER))
    f['db'].commit()
    consultations.developer_marker(actor, {'channel_id': CB, 'conversation_id': DEVELOPER, 'enabled': True})
    result, _ids = _case(f, [RU], expected='en', automatic=False, profile=actor,
        tenant=B, channel=CB, lead=42)
    selection = _assert_language(f, result, 'en', method='legacy')
    assert selection['policy_source'] == 'legacy' and selection['policy_version'] == 0
    assert languages.read(actor)['source'] == 'legacy'
    assert not f['db'].execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='summary_language'", (B,))


def test_fixed_company_policy_and_later_locale_policy_changes_preserve_saved_text(language_db):
    f = language_db
    _policy(mode='fixed', fixed='ru')
    result, _ids = _case(f, [AZ], ui='az', expected='ru')
    selection = _assert_language(f, result, 'ru', method='company_fixed')
    before = {key: result['ticket'][key] for key in ('description', 'summary', 'suggestions', 'next_steps', 'summary_hash', 'summary_language')}
    _policy(mode='fixed', fixed='en')
    reopened = cases.detail({**PROFILE, 'locale': 'en', 'language': 'en'}, result['ticket']['id'])
    assert {key: reopened['ticket'][key] for key in before} == before
    assert selection['policy_version'] == 1
    f['provider'].assert_called_once()


def _regenerate(result, *, language=None, request_id=None):
    return {'request_id': request_id or str(uuid.uuid4()), 'confirm_ai_cost': True,
        'expected_summary_hash': result['ticket']['summary_hash'], **({'language': language} if language else {})}


def test_regeneration_explicit_language_wins_and_preserves_previous_manual_version(language_db):
    f = language_db
    _policy(mode='fixed', fixed='ru')
    result, _ids = _case(f, [AZ], expected='ru')
    ticket_id = result['ticket']['id']
    cases.update(PROFILE, ticket_id, {'description': 'Exact employee edit SKU-42 / 49.00'})
    before = cases.detail(PROFILE, ticket_id)
    previous = {key: before['ticket'][key] for key in ('description', 'summary', 'suggestions', 'next_steps')}
    f['output_language'] = 'en'
    request = _regenerate(before, language='en')
    regenerated = summaries.regenerate({**PROFILE, 'locale': 'az'}, ticket_id, request)
    _assert_language(f, regenerated, 'en', method='explicit')
    assert regenerated['summary_request'] == {'request_id': request['request_id'], 'state': 'applied'}
    matching = [version for version in regenerated['summary_history'] if version.get('request_id') == request['request_id']]
    assert len(matching) == 1 and matching[0]['state'] == 'applied'
    assert matching[0]['previous'] == previous
    assert matching[0]['language']['policy_version'] == 1
    assert f['provider'].call_count == 2
    replayed = summaries.regenerate(PROFILE, ticket_id, request)
    assert replayed['ticket']['summary_hash'] == regenerated['ticket']['summary_hash']
    assert replayed['summary_history'] == regenerated['summary_history']
    assert f['provider'].call_count == 2
    with pytest.raises(TenantLinearError) as changed:
        summaries.regenerate(PROFILE, ticket_id, {**request, 'language': 'tr'})
    assert changed.value.status == 409 and f['provider'].call_count == 2


def test_stale_summary_hash_is_rejected_before_provider_and_preserves_employee_edit(language_db):
    f = language_db
    result, _ids = _case(f, [AZ])
    ticket_id = result['ticket']['id']
    command = _regenerate(result, language='en')
    cases.update(PROFILE, ticket_id, {'description': 'Concurrent employee description'})
    before = cases.detail(PROFILE, ticket_id)
    with pytest.raises(TenantLinearError) as stale:
        summaries.regenerate(PROFILE, ticket_id, command)
    assert stale.value.status == 409
    after = cases.detail(PROFILE, ticket_id)
    assert after['ticket']['summary_hash'] == before['ticket']['summary_hash']
    assert after['summary_history'] == before['summary_history']
    f['provider'].assert_called_once()


def test_employee_edit_during_generation_saves_comparison_without_overwriting_current_text(language_db):
    f = language_db
    result, _ids = _case(f, [AZ])
    ticket_id = result['ticket']['id']
    command = _regenerate(result, language='en')
    original = copy.deepcopy(result['ticket'])
    response = f['provider'].side_effect

    def during(*args, **kwargs):
        cases.update(PROFILE, ticket_id, {'description': 'Concurrent manual edit, preserve verbatim',
                                       'next_steps': 'Call the customer after checking SKU-42'})
        return response(*args, **kwargs)

    f['output_language'] = 'en'
    f['provider'].side_effect = during
    after = summaries.regenerate(PROFILE, ticket_id, command)
    assert after['ticket']['description'] == 'Concurrent manual edit, preserve verbatim'
    assert after['ticket']['next_steps'] == 'Call the customer after checking SKU-42'
    assert after['ticket']['summary'] == original['summary']
    assert after['ticket']['summary_language'] is None
    assert after['summary_request'] == {'request_id': command['request_id'], 'state': 'conflict'}
    proposals = [version for version in after['summary_history'] if version.get('request_id') == command['request_id']]
    assert len(proposals) == 1 and proposals[0]['state'] == 'conflict'
    assert SUMMARY['en'] in proposals[0]['proposal']['summary']
    assert proposals[0]['language']['language'] == 'en'
    assert f['provider'].call_count == 2
    summaries.regenerate(PROFILE, ticket_id, command)
    assert f['provider'].call_count == 2


def test_unknown_regeneration_is_not_paid_again_on_same_request(language_db):
    f = language_db
    result, _ids = _case(f, [AZ])
    command = _regenerate(result, language='en')
    f['provider'].side_effect = TimeoutError('Synthetic provider outcome unknown')
    with pytest.raises(TimeoutError):
        summaries.regenerate(PROFILE, result['ticket']['id'], command)
    assert f['provider'].call_count == 2
    after = summaries.regenerate(PROFILE, result['ticket']['id'], command)
    assert f['provider'].call_count == 2
    assert after['ticket']['summary'] == result['ticket']['summary']
    history = [version for version in after['summary_history'] if version.get('request_id') == command['request_id']]
    assert len(history) == 1 and history[0]['state'] == 'unavailable'
    assert history[0]['request_state'] == 'unknown'
    assert after['summary_request'] == {'request_id': command['request_id'], 'state': 'unknown'}
    assert after['ticket']['summary_language'] == result['ticket']['summary_language']
    assert f['db'].execute("SELECT count(*) AS n FROM saas_ai_agent_runs WHERE state='unknown'")[0]['n'] == 1


def test_real_support_regeneration_route_requires_cost_confirmation_hash_and_current_scope(language_db):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from tenant_support_inbox_api import handler
    f = language_db
    result, _ids = _case(f, [AZ])
    ticket_id = result['ticket']['id']

    async def scenario():
        actor = dict(PROFILE)
        path = '/api/platform/ai/support-tickets/' + ticket_id + '/summary'
        # The real handler reads the route parameter, as the application's route does.
        app = web.Application()
        app.router.add_post('/api/platform/ai/support-tickets/{ticket_id}/summary',
                           handler(lambda _: actor, 'https://crm.test', LOGGER, 'summary'))
        expected = {'expected_tenant_id': A, 'expected_user_id': 11}
        command = {**_regenerate(result, language='en'), **expected}
        async with TestClient(TestServer(app)) as client:
            assert (await client.post(path, headers={'Origin': 'https://crm.test'}, json=expected)).status == 400
            assert (await client.post(path, headers={'Origin': 'https://crm.test'}, json={**command, 'confirm_ai_cost': False})).status == 400
            assert (await client.post(path, headers={'Origin': 'https://crm.test'}, json={**command, 'expected_tenant_id': B})).status == 409
            f['output_language'] = 'en'
            response = await client.post(path, headers={'Origin': 'https://crm.test'}, json=command)
            payload = await response.json()
            assert response.status == 200, payload
            assert payload['result']['ticket']['summary_language']['language'] == 'en'
            assert (await client.post(path, headers={'Origin': 'https://crm.test'}, json=command)).status == 200
            assert f['provider'].call_count == 2
            actor.update(tenant_id=B, telegram_id=22)
            assert (await client.post(path, headers={'Origin': 'https://crm.test'}, json={
                **command, 'expected_tenant_id': B, 'expected_user_id': 22})).status == 404
            assert f['provider'].call_count == 2

    asyncio.run(scenario())


def test_one_night_report_keeps_each_saved_customer_summary_language(language_db):
    from datetime import datetime, timezone
    f = language_db
    az, _ids = _case(f, [AZ], expected='az')
    f['db'].execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,'second-client',43,'Second customer')", (A, CA))
    f['db'].execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state) VALUES(%s::uuid,'connect',%s,'second-client',%s::uuid,'active')", (A, CA, AGENT))
    f['db'].commit()
    ru, _ids = _case(f, [RU], expected='ru', lead=43)
    now = datetime.now(timezone.utc)
    report = night_report.report({**PROFILE, 'language': 'en'}, now.date().isoformat(),
        'UTC', '00:00', '23:59', now=now)
    # The report is a read projection; saved customer summaries remain diverse.
    rows = {str(row['id']): row for row in report['tickets']}
    assert rows[az['ticket']['id']]['summary'] == az['ticket']['summary']
    assert rows[ru['ticket']['id']]['summary'] == ru['ticket']['summary']
    assert SUMMARY['az'] in rows[az['ticket']['id']]['summary']
    assert SUMMARY['ru'] in rows[ru['ticket']['id']]['summary']
    assert f['provider'].call_count == 2


def _candidate_response(language, refs):
    return {'title': SUMMARY[language], 'symptoms': [SUMMARY[language]],
        'cause': {'status': 'unknown', 'text': ''}, 'resolution_steps': [],
        'outcome': {'status': 'unconfirmed', 'text': SUMMARY[language], 'message_ids': [refs[-1]]},
        'source_message_ids': refs, 'product': 'Bein', 'product_version': '2.1',
        'clarification_note': '', 'case_relation': 'new_request', 'work_status': 'unresolved'}


@pytest.mark.parametrize('client,ui,expected,fixed,explicit', [
    (AZ, 'ru', 'az', None, None), (RU, 'az', 'ru', None, None),
    (EN, 'ru', 'en', None, None), (TR, 'ru', 'tr', None, None),
    (AZ, 'az', 'ru', 'ru', None), (RU, 'ru', 'tr', 'ru', 'tr')])
def test_actual_training_candidate_generation_uses_same_language_policy(language_db, monkeypatch, client, ui, expected, fixed, explicit):
    f = language_db
    _policy(mode='fixed' if fixed else 'automatic', fixed=fixed)
    source_ids = _messages(f, [client, {'body': 'Сотрудник проверит подключение завтра.', 'direction': 'outgoing'}])

    def generated(_key, _model, mode, messages, _draft, **kwargs):
        assert mode == 'knowledge_extraction'
        assert kwargs['extra_context']['output_language'] == expected
        assert languages.LANGUAGES[expected] in kwargs['instructions']
        return json.dumps({'cards': [_candidate_response(expected, [row['ref'] for row in messages])]}, ensure_ascii=False), {
            'input_tokens': 20, 'output_tokens': 5, 'total_tokens': 25}

    generate = Mock(side_effect=generated)
    monkeypatch.setattr(candidates, 'generate', generate)
    result = candidates.analyze({**PROFILE, 'locale': ui, 'language': ui}, {'request_id': str(uuid.uuid4()),
        'provider': 'connect', 'channel_id': CA, 'conversation_id': 'client',
        'message_ids': source_ids, 'analysis_agent_id': AGENT,
        **({'language': explicit, 'language_explicit': True} if explicit else {})})
    generate.assert_called_once()
    assert len(result['candidates']) == 1, (result['request'], generate.call_args.kwargs['extra_context'].get('output_language'),
        {code: label in generate.call_args.kwargs['instructions'] for code, label in languages.LANGUAGES.items()})
    card = result['candidates'][0]
    assert card['content_language'] == expected
    assert SUMMARY[expected] in card['title']
    saved = candidate_store.detail(PROFILE, card['id'])
    assert saved['candidate']['content_language'] == expected
    selection = result['request']['route']['summary_language']
    assert selection['language'] == expected and selection['policy_version'] == 1
    assert selection['method'] == ('explicit' if explicit else 'company_fixed' if fixed else 'customer_detected')
    _policy(mode='fixed', fixed='en' if expected != 'en' else 'ru')
    reloaded = candidate_store.detail({**PROFILE, 'locale': 'en'}, card['id'])
    assert reloaded['candidate']['content_language'] == expected and reloaded['candidate']['title'] == saved['candidate']['title']
    generate.assert_called_once()


def _training(f, monkeypatch, messages, *, expected, **extra):
    """Actual bounded snapshot, admission, generation receipt and saved candidate."""
    source_ids = _messages(f, messages)

    def generated(_key, _model, mode, rows, _draft, **kwargs):
        assert mode == 'knowledge_extraction'
        assert kwargs['extra_context']['output_language'] == expected
        return json.dumps({'cards': [_candidate_response(expected, [row['ref'] for row in rows])]},
                          ensure_ascii=False), {'total_tokens': 25}

    generate = Mock(side_effect=generated)
    monkeypatch.setattr(candidates, 'generate', generate)
    data = {'request_id': str(uuid.uuid4()), 'provider': 'connect', 'channel_id': CA,
            'conversation_id': 'client', 'message_ids': source_ids, 'analysis_agent_id': AGENT, **extra}
    result = candidates.analyze({**PROFILE, 'locale': 'en', 'language': 'en'}, data)
    assert len(result['candidates']) == 1, result['request']
    return result, generate, data


@pytest.mark.parametrize('marker,expected', [(None, 'ru'), (False, 'ru'), (True, 'az')])
def test_old_cached_ui_language_is_ignored_until_employee_explicitly_selects_language(language_db, monkeypatch, marker, expected):
    f = language_db
    _policy()
    result, generation, _data = _training(f, monkeypatch, [RU], expected=expected, language='az',
        **({'language_explicit': marker} if marker is not None else {}))
    selected = result['request']['route']['summary_language']
    assert selected['language'] == expected
    assert selected['method'] == ('explicit' if marker else 'customer_detected')
    generation.assert_called_once()


@pytest.mark.parametrize('mode,expected,incomplete', [
    ('current', 'en', False), ('missing_version', 'az', True), ('replaced', 'az', True)])
def test_support_legacy_voice_requires_completed_exact_current_media_version(language_db, mode, expected, incomplete):
    f = language_db
    result, _ids = _case(f, [{'body': EN, 'kind': 'voice', 'legacy_voice': mode,
        'raw': {'mediaId': 'original-synthetic-voice', 'fileLength': 123}}], expected=expected)
    selected = _assert_language(f, result, expected,
        method='customer_detected' if mode == 'current' else 'fallback')
    assert selected['media_incomplete'] is incomplete
    assert f['provider'].call_args.kwargs['extra_context']['media_incomplete'] is incomplete
    f['provider'].assert_called_once()


@pytest.mark.parametrize('kind,receipt', [
    ('image', {'status': 'processed', 'code': 'observed', 'observation': EN * 4}),
    ('voice', {'status': 'partial', 'code': 'partial', 'transcript': EN * 4})])
def test_support_machine_observation_and_partial_audio_cannot_vote_as_customer_speech(language_db, kind, receipt):
    f = language_db
    result, _ids = _case(f, [{'body': '', 'kind': kind,
        'raw': {'mediaId': 'synthetic-saved-evidence'}, 'saved_receipt': receipt}])
    selected = _assert_language(f, result, 'az', method='fallback')
    assert selected['media_incomplete'] is (kind == 'voice')
    f['provider'].assert_called_once()


@pytest.mark.parametrize('kind,status', [('image', 'processed'), ('voice', 'partial')])
def test_training_reuses_saved_media_without_treating_english_machine_output_as_customer_language(language_db, monkeypatch, kind, status):
    f = language_db
    _policy()
    ids = _messages(f, [{'body': '', 'kind': kind, 'raw': {'mediaId': 'saved-machine-output'}}])
    data = {'provider': 'connect', 'channel_id': CA, 'conversation_id': 'client',
            'message_ids': ids, 'analysis_agent_id': AGENT}
    with f['factory']() as conn, conn.cursor() as cur:
        snapshot = candidate_store.snapshot(cur, PROFILE, data)
        ref = snapshot['refs'][0]
        receipt = {'id': ref['id'], 'status': status, 'code': 'observed' if kind == 'image' else 'partial',
                   'observation' if kind == 'image' else 'transcript': EN * 4}
        evidence.persist(cur, A, [ref], [receipt])

    def generated(_key, _model, mode, rows, _draft, **kwargs):
        assert mode in {'knowledge_extraction', 'knowledge_improvement'}
        assert kwargs['extra_context']['output_language'] == 'az'
        # The model can read the saved observation, but it is not customer speech.
        assert EN in rows[0]['text']
        assert not kwargs.get('images')
        if mode == 'knowledge_improvement':
            return json.dumps({'title': SUMMARY['az'], 'symptoms': [SUMMARY['az']], 'cause_text': '',
                'resolution_steps': [], 'operator_actions': [], 'outcome_text': SUMMARY['az']}), {'total_tokens': 25}
        return json.dumps({'cards': [_candidate_response('az', [row['ref'] for row in rows])]},
                          ensure_ascii=False), {'total_tokens': 25}

    generation = Mock(side_effect=generated)
    monkeypatch.setattr(candidates, 'generate', generation)
    result = candidates.analyze(PROFILE, {**data, 'request_id': str(uuid.uuid4())})
    assert len(result['candidates']) == 1, result['request']
    assert result['request']['route']['summary_language']['method'] == 'fallback'
    assert result['candidates'][0]['content_language'] == 'az'
    generation.assert_called_once()
    if kind == 'image':
        value = result['candidates'][0]
        proposed = improve.improve({**PROFILE, 'locale': 'en'}, value['id'], {
            'request_id': str(uuid.uuid4()), 'expected_version': value['version'], 'mode': 'improve'})
        assert proposed['language_selection']['language'] == 'az'
        assert proposed['language_selection']['method'] == 'fallback'
        assert proposed['proposal']['content_language'] == 'az'
        assert candidate_store.detail(PROFILE, value['id'])['candidate']['version'] == value['version']
        assert generation.call_count == 2


def test_employee_summary_fields_detach_current_ai_label_and_preserve_exact_ai_history(language_db):
    f = language_db
    result, _ids = _case(f, [AZ])
    ticket_id = result['ticket']['id']
    history = copy.deepcopy(result['summary_history'])
    cases.update(PROFILE, ticket_id, {'description': RU, 'suggestions': 'Проверить SKU-42 / 49.00'})
    after = cases.detail({**PROFILE, 'locale': 'en'}, ticket_id)
    assert after['ticket']['description'] == RU
    assert after['ticket']['summary'] == result['ticket']['summary']
    assert after['ticket']['summary_language'] is None
    assert after['summary_history'] == history
    assert history[0]['language']['language'] == 'az'
    assert history[0]['applied_content']['summary'] == result['ticket']['summary']
    f['provider'].assert_called_once()


def test_matching_applied_language_survives_more_than_ten_later_unknown_requests(language_db):
    f = language_db
    result, _ids = _case(f, [AZ])
    f['provider'].side_effect = TimeoutError('Synthetic unknown outcome, never retry automatically')
    requests = []
    for _ in range(11):
        request = _regenerate(result, language='en')
        requests.append(request)
        with pytest.raises(TimeoutError):
            summaries.regenerate(PROFILE, result['ticket']['id'], request)
    after = summaries.regenerate(PROFILE, result['ticket']['id'], requests[0])
    assert after['summary_request'] == {'request_id': requests[0]['request_id'], 'state': 'unknown'}
    assert after['ticket']['summary_language'] == result['ticket']['summary_language']
    assert after['ticket']['summary_hash'] == result['ticket']['summary_hash']
    assert len(after['summary_history']) == 10
    assert all(version['state'] == 'unavailable' for version in after['summary_history'])
    assert all(version.get('request_id') != requests[0]['request_id'] for version in after['summary_history'])
    assert f['provider'].call_count == 12


@pytest.mark.parametrize('fixed,explicit,expected', [('ru', None, 'ru'), ('ru', 'tr', 'tr'), (None, None, 'en')])
def test_actual_improvement_requires_explicit_apply_preserves_versions_and_language_provenance(language_db, monkeypatch, fixed, explicit, expected):
    f = language_db
    _policy()
    initial, generation, _data = _training(f, monkeypatch, [EN, {'body': RU, 'direction': 'outgoing'}], expected='en')
    value = initial['candidates'][0]
    original = candidate_store.detail(PROFILE, value['id'])['candidate']
    if fixed:
        _policy(mode='fixed', fixed=fixed)
    proposal_text = SUMMARY[expected] + ' SKU-42 / 49.00'

    def generated(_key, _model, mode, _rows, _draft, **kwargs):
        assert mode == 'knowledge_improvement'
        assert kwargs['extra_context']['output_language'] == expected
        assert kwargs['extra_context']['summary_language']['language'] == expected
        return json.dumps({'title': proposal_text, 'symptoms': [proposal_text], 'cause_text': '',
            'resolution_steps': [], 'operator_actions': [], 'outcome_text': proposal_text},
            ensure_ascii=False), {'total_tokens': 25}

    generation.side_effect = generated
    command = {'request_id': str(uuid.uuid4()), 'expected_version': value['version'],
        'mode': 'improve', 'language': explicit or 'az', 'clarifications': 'Employee note in Russian: проверить завтра',
        **({'language_explicit': True} if explicit else {})}
    proposed = improve.improve({**PROFILE, 'locale': 'ru'}, value['id'], command)
    assert proposed['requires_explicit_save'] is True and proposed['proposal']['content_language'] == expected
    assert proposed['language_selection']['method'] == ('explicit' if explicit else 'company_fixed' if fixed else 'customer_detected')
    assert candidate_store.detail(PROFILE, value['id'])['candidate'] == original
    replayed = improve.improve(PROFILE, value['id'], command)
    assert replayed['proposal'] == proposed['proposal'] and generation.call_count == 2
    saved = candidate_store.mutate(PROFILE, value['id'], {'expected_version': value['version'], **proposed['proposal']})['candidate']
    assert saved['version'] == value['version'] + 1
    assert saved['title'] == proposal_text and saved['content_language'] == expected
    assert saved['language_selection'] == proposed['language_selection']
    assert saved['language_history'][0]['previous_version'] == value['version']
    assert saved['language_history'][0]['previous_content']['title'] == original['title']
    assert saved['language_history'][0]['content']['title'] == proposal_text
    with pytest.raises(TenantLinearError) as stale:
        candidate_store.mutate(PROFILE, value['id'], {'expected_version': value['version'], **proposed['proposal']})
    assert stale.value.status == 409
    _policy(mode='fixed', fixed='az')
    reopened = candidate_store.detail({**PROFILE, 'locale': 'az'}, value['id'])['candidate']
    assert reopened['title'] == proposal_text and reopened['language_selection'] == proposed['language_selection']
    assert generation.call_count == 2


def test_unified_group_language_excludes_employee_registered_only_on_historical_source_channel(language_db, monkeypatch):
    f = language_db
    _policy()
    group = '120363400000888@g.us'
    f['db'].execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Synthetic historical channel')", (A, CB))
    for channel, lead in ((CA, 43), (CB, 44)):
        f['db'].execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,%s,'group')", (A, channel, group, lead))
    f['db'].execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
        (json.dumps([{'provider': 'connect', 'channel_id': CA, 'allow_group_messages': True}]), A, AGENT))
    f['db'].execute("INSERT INTO saas_ai_group_staff(tenant_id,channel_id,jid,member_id,verified_by) VALUES(%s::uuid,%s::uuid,'15550001111@s.whatsapp.net',33,11)", (A, CB))
    f['db'].commit()
    groups.configure(PROFILE, {'conversation_id': group, 'expected_revision': 0,
        'assignments': [{'agent_id': AGENT, 'channel_id': CA, 'priority': 0}]})
    _messages(f, [{'body': EN, 'raw': {'participantJid': '15550009999@s.whatsapp.net'}}], lead=43)
    _messages(f, [{'body': RU * 6, 'raw': {'participantJid': '15550001111@s.whatsapp.net'}}], channel=CB, lead=44)
    command = {'request_id': str(uuid.uuid4()), 'provider': 'connect', 'channel_id': CA,
        'conversation_id': group, 'history_mode': 'unified_group', 'analysis_agent_id': AGENT}
    with f['factory']() as conn, conn.cursor() as cur:
        snapshot = candidate_store.snapshot(cur, PROFILE, command)
    assert {ref['channel_id'] for ref in snapshot['refs']} == {CA, CB}
    assert any(row['summary_language_role'] == 'operator' and RU in row['text']
               for row in snapshot['messages'])

    def generated(_key, _model, mode, rows, _draft, **kwargs):
        assert mode == 'knowledge_extraction'
        assert kwargs['extra_context']['output_language'] == 'en'
        assert all(set(row) == {'ref', 'role', 'text'} for row in rows)
        assert any(EN in row['text'] for row in rows) and any(RU in row['text'] for row in rows)
        return json.dumps({'cards': [_candidate_response('en', [row['ref'] for row in rows])]}), {'total_tokens': 25}

    generation = Mock(side_effect=generated)
    monkeypatch.setattr(candidates, 'generate', generation)
    result = candidates.analyze(PROFILE, command)
    assert set(result['request']['route']['source_channels']) == {CA, CB}
    assert result['request']['route']['summary_language']['language'] == 'en'
    assert result['candidates'][0]['content_language'] == 'en'
    generation.assert_called_once()


@pytest.mark.parametrize('state', ['completed', 'unknown'])
def test_preupgrade_extraction_digest_new_language_annotations_do_not_admit_another_paid_attempt(language_db, monkeypatch, state):
    f = language_db
    ids = _messages(f, [AZ])
    data = {'request_id': str(uuid.uuid4()), 'provider': 'connect', 'channel_id': CA,
        'conversation_id': 'client', 'message_ids': ids, 'analysis_agent_id': AGENT, 'language': 'az'}

    def generated(_key, _model, _mode, rows, _draft, **_kwargs):
        if state == 'unknown':
            raise TimeoutError('Synthetic preupgrade provider outcome unknown')
        return json.dumps({'cards': [_candidate_response('az', [row['ref'] for row in rows])]}), {'total_tokens': 25}

    generation = Mock(side_effect=generated)
    monkeypatch.setattr(candidates, 'generate', generation)
    original = candidates.analyze(PROFILE, data)
    assert original['request']['status'] == state
    with f['factory']() as conn, conn.cursor() as cur:
        snapshot = candidate_store.snapshot(cur, PROFILE, data)
    old_messages = [{key: value for key, value in row.items()
        if key not in {'summary_language_role', 'customer_language_text'}} for row in snapshot['messages']]
    old_digest = candidate_store.digest(old_messages)
    stored = f['db'].execute('SELECT source_digest,fingerprint FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',
        (A, original['request']['id']))[0]
    assert stored['source_digest'] == old_digest
    # Persist the exact pre-feature fingerprint and route shape, without the
    # two newly derived language annotations. Existing receipts stay authoritative.
    old_fingerprint = candidate_store.digest([
        {'provider': 'connect', 'conversation_id': 'client', 'history_mode': 'channel', 'channel_id': CA},
        snapshot['refs'], old_digest, AGENT, 'summary-fixture-model', 'az', None])
    assert stored['fingerprint'] == old_fingerprint
    f['db'].execute("""UPDATE saas_knowledge_extractions SET source_digest=%s,fingerprint=%s,
      route=route-'summary_language'-'summary_language_policy'-'requested_summary_language'
      WHERE tenant_id=%s::uuid AND id=%s::uuid""", (old_digest, old_fingerprint, A, original['request']['id']))
    f['db'].commit()
    repeated = candidates.analyze({**PROFILE, 'locale': 'ru'}, {**data, 'request_id': str(uuid.uuid4())})
    assert repeated['request']['id'] == original['request']['id']
    assert repeated['request']['status'] == state
    generation.assert_called_once()
    assert f['db'].execute('SELECT count(*) AS n FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid', (A,))[0]['n'] == 1


@pytest.mark.parametrize('language', [None, 'tr'])
def test_full_reload_restores_unknown_regeneration_exact_immutable_command_without_another_paid_call(language_db, language):
    f = language_db
    page, _ids = _case(f, [AZ])
    ticket_id = page['ticket']['id']
    command = _regenerate(page, language=language)
    normalized = {**command, 'language': language}
    f['provider'].side_effect = TimeoutError('Synthetic unknown provider outcome before browser reload')
    with pytest.raises(TimeoutError):
        summaries.regenerate(PROFILE, ticket_id, command)
    restored = cases.detail({**PROFILE, 'locale': 'en'}, ticket_id)
    assert restored['summary_pending_request'] == {'command': normalized, 'state': 'unknown'}
    restored['summary_pending_request']['command']['language'] = 'ru'
    cases.update(PROFILE, ticket_id, {'description': 'Employee edit made after the uncertain request'})
    _policy(mode='fixed', fixed='en')
    reloaded = cases.detail({**PROFILE, 'locale': 'ru'}, ticket_id)
    assert reloaded['summary_pending_request'] == {'command': normalized, 'state': 'unknown'}
    assert reloaded['ticket']['summary_hash'] != normalized['expected_summary_hash']
    replayed = summaries.regenerate(PROFILE, ticket_id, reloaded['summary_pending_request']['command'])
    assert replayed['summary_request'] == {'request_id': command['request_id'], 'state': 'unknown'}
    assert replayed['summary_pending_request'] == reloaded['summary_pending_request']
    with pytest.raises(TenantLinearError) as changed:
        summaries.regenerate(PROFILE, ticket_id, {**normalized, 'language': 'en'})
    assert changed.value.status == 409
    assert f['provider'].call_count == 2


def test_detail_restores_pending_real_run_during_generation_and_clears_it_after_apply(language_db):
    f = language_db
    page, _ids = _case(f, [AZ])
    ticket_id = page['ticket']['id']
    command = _regenerate(page)
    normalized = {**command, 'language': None}
    generated = f['provider'].side_effect
    pending = []

    def during(*args, **kwargs):
        current = cases.detail(PROFILE, ticket_id)
        pending.append(current['summary_pending_request'])
        assert pending[-1] == {'command': normalized, 'state': 'pending'}
        assert current['ticket']['summary_state'] == 'generating'
        assert cases.detail({'tenant_id': A, 'telegram_id': 33}, ticket_id)['summary_pending_request'] is None
        replay = summaries.regenerate(PROFILE, ticket_id, command)
        assert replay['summary_request'] == {'request_id': command['request_id'], 'state': 'pending'}
        assert replay['summary_pending_request'] == pending[-1]
        assert f['provider'].call_count == 2
        return generated(*args, **kwargs)

    f['provider'].side_effect = during
    applied = summaries.regenerate(PROFILE, ticket_id, command)
    assert len(pending) == 1
    assert applied['summary_request'] == {'request_id': command['request_id'], 'state': 'applied'}
    assert applied['summary_pending_request'] is None
    assert cases.detail(PROFILE, ticket_id)['summary_pending_request'] is None
    assert f['provider'].call_count == 2


def test_unresolved_employee_command_survives_more_than_ten_other_employee_results(language_db):
    f = language_db
    page, _ids = _case(f, [AZ])
    ticket_id = page['ticket']['id']
    generated = f['provider'].side_effect
    command = _regenerate(page, language='tr')
    f['provider'].side_effect = TimeoutError('Synthetic owner request outcome unknown')
    with pytest.raises(TimeoutError):
        summaries.regenerate(PROFILE, ticket_id, command)
    f['provider'].side_effect = generated
    f['output_language'] = 'en'
    employee = {'tenant_id': A, 'telegram_id': 33}
    for _ in range(11):
        current = cases.detail(employee, ticket_id)
        assert current['summary_pending_request'] is None
        applied = summaries.regenerate(employee, ticket_id, _regenerate(current, language='en'))
        assert applied['summary_request']['state'] == 'applied'
    reloaded = cases.detail(PROFILE, ticket_id)
    assert len(reloaded['summary_history']) == 10
    assert all(version.get('request_id') != command['request_id'] for version in reloaded['summary_history'])
    assert reloaded['summary_pending_request'] == {'command': command, 'state': 'unknown'}
    assert cases.detail(employee, ticket_id)['summary_pending_request'] is None
    replayed = summaries.regenerate(PROFILE, ticket_id, command)
    assert replayed['summary_request']['state'] == 'unknown'
    assert f['provider'].call_count == 13


@pytest.mark.parametrize('terminal', ['applied', 'conflict', 'blocked'])
def test_latest_terminal_request_does_not_resurrect_older_unknown_command(language_db, terminal):
    f = language_db
    page, _ids = _case(f, [AZ])
    ticket_id = page['ticket']['id']
    generated = f['provider'].side_effect
    old = _regenerate(page, language='en')
    f['provider'].side_effect = TimeoutError('Synthetic earlier request remains unknown')
    with pytest.raises(TimeoutError):
        summaries.regenerate(PROFILE, ticket_id, old)
    assert cases.detail(PROFILE, ticket_id)['summary_pending_request']['command'] == old
    latest = _regenerate(cases.detail(PROFILE, ticket_id), language='tr')
    f['output_language'] = 'tr'
    if terminal == 'blocked':
        # A real reservation is invalidated before provider dispatch; finish
        # records its durable blocked status even though the run is unknown.
        item = summaries.reserve(A, ticket_id, PROFILE, retry=True, command=latest)
        assert item and cases.detail(PROFILE, ticket_id)['summary_pending_request']['state'] == 'pending'
        cases.update(PROFILE, ticket_id, {'description': 'Edited before dispatch; no paid attempt'})
        summaries.finish(item, None, {})
        result = summaries.regenerate(PROFILE, ticket_id, latest)
    else:
        def complete(*args, **kwargs):
            if terminal == 'conflict':
                cases.update(PROFILE, ticket_id, {'description': 'Keep this exact concurrent employee edit'})
            return generated(*args, **kwargs)
        f['provider'].side_effect = complete
        result = summaries.regenerate(PROFILE, ticket_id, latest)
    assert result['summary_request'] == {'request_id': latest['request_id'], 'state': terminal}
    assert result['summary_pending_request'] is None
    older_replay = summaries.regenerate(PROFILE, ticket_id, old)
    assert older_replay['summary_request']['state'] == 'unknown'
    assert older_replay['summary_pending_request'] is None
    assert cases.detail(PROFILE, ticket_id)['summary_pending_request'] is None
    assert f['provider'].call_count == (2 if terminal == 'blocked' else 3)


def test_restored_summary_command_is_scoped_to_exact_case_tenant_and_authorized_employee(language_db):
    f = language_db
    page, _ids = _case(f, [AZ])
    ticket_id = page['ticket']['id']
    generated = f['provider'].side_effect
    command = _regenerate(page, language='en')
    f['provider'].side_effect = TimeoutError('Synthetic owner-only pending operation')
    with pytest.raises(TimeoutError):
        summaries.regenerate(PROFILE, ticket_id, command)
    other_employee = cases.detail({'tenant_id': A, 'telegram_id': 33, 'role': 'owner'}, ticket_id)
    assert other_employee['ticket']['id'] == ticket_id
    assert other_employee['summary_pending_request'] is None
    for profile, status in (({'tenant_id': B, 'telegram_id': 22}, 404), ({'tenant_id': B, 'telegram_id': 11}, 403)):
        with pytest.raises(TenantLinearError) as foreign:
            cases.detail(profile, ticket_id)
        assert foreign.value.status == status
    f['db'].execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'other-summary-customer',43)", (A, CA))
    f['db'].execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state) VALUES(%s::uuid,'connect',%s,'other-summary-customer',%s::uuid,'active')", (A, CA, AGENT))
    f['db'].commit()
    f['provider'].side_effect = generated
    other_case, _ = _case(f, [AZ], lead=43)
    assert other_case['ticket']['id'] != ticket_id and other_case['summary_pending_request'] is None
    assert cases.detail(PROFILE, ticket_id)['summary_pending_request']['command'] == command
    f['db'].execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=11', (A,))
    f['db'].commit()
    with pytest.raises(TenantLinearError) as revoked:
        cases.detail(PROFILE, ticket_id)
    assert revoked.value.status == 403
    assert f['provider'].call_count == 3


@pytest.mark.parametrize('state', ['applied', 'unknown'])
def test_case_uuid_deep_link_aliases_preserve_summary_history_pending_command_and_replay_receipt(language_db, state):
    f = language_db
    page, _ids = _case(f, [AZ])
    ticket_id = page['ticket']['id']
    command = _regenerate(page, language='en')
    if state == 'unknown':
        f['provider'].side_effect = TimeoutError('Synthetic unknown generation before opening a UUID alias')
        with pytest.raises(TimeoutError):
            summaries.regenerate(PROFILE, ticket_id, command)
    else:
        f['output_language'] = 'en'
        applied = summaries.regenerate(PROFILE, ticket_id, command)
        assert applied['summary_request'] == {'request_id': command['request_id'], 'state': 'applied'}
    canonical = cases.detail(PROFILE, ticket_id)
    assert canonical['ticket']['summary_language']['language'] == ('en' if state == 'applied' else 'az')
    assert canonical['summary_pending_request'] == (
        {'command': command, 'state': 'unknown'} if state == 'unknown' else None)
    aliases = (ticket_id.upper(), '{' + ticket_id.upper() + '}',
               uuid.UUID(ticket_id).hex, uuid.UUID(ticket_id).urn)
    fields = ('id', 'description', 'summary', 'suggestions', 'next_steps', 'summary_hash', 'summary_language')
    for alias in aliases:
        opened = cases.detail({**PROFILE, 'locale': 'ru'}, alias)
        assert {key: opened['ticket'][key] for key in fields} == {key: canonical['ticket'][key] for key in fields}
        assert opened['summary_history'] == canonical['summary_history']
        assert opened['summary_pending_request'] == canonical['summary_pending_request']
        replayed = summaries.regenerate(PROFILE, alias, command)
        assert replayed['summary_request'] == {'request_id': command['request_id'], 'state': state}
        assert replayed['summary_history'] == canonical['summary_history']
        assert replayed['summary_pending_request'] == canonical['summary_pending_request']
        assert replayed['ticket']['summary_hash'] == canonical['ticket']['summary_hash']
        with pytest.raises(TenantLinearError) as foreign:
            cases.detail({'tenant_id': B, 'telegram_id': 22}, alias)
        assert foreign.value.status == 404
    assert f['provider'].call_count == 2
    assert f['db'].execute("""SELECT count(*) AS n FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid
      AND entity_type='support_ticket' AND entity_id=%s AND action='support_summary_requested'""",
      (A, ticket_id))[0]['n'] == 1
