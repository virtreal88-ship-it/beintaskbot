import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock
import pytest
import tenant_operator_copilot as copilot
import tenant_operator_copilot_learning as learning
from tenant_linear_policy import TenantLinearError

AGENT = 'bbbbbbbb-bbbb-4bbb-abbb-bbbbbbbbbbbb'
TENANT = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'


def command(prompt='Проверили после подключения: причина в интернете; подготовь ответ клиенту.', **changes):
    return {'mode': 'chat', 'prompt': prompt, 'request_id': str(uuid.uuid4()),
        '_agent_id': AGENT, 'channel_id': str(uuid.uuid4()), 'conversation_id': 'case-1',
        'lead_id': 17, **changes}


@pytest.mark.parametrize('prompt', [
    'Может интернет?', 'Если проверили интернет, попроси AnyDesk',
    'Попроси AnyDesk', 'Проверили, возможно проблема в интернете',
    'Клиент написал: проверили интернет', 'Клиент сказал: проверили интернет',
    '"Проверили интернет" — это текст клиента', 'Maybe we confirmed the cause?',
])
def test_hypotheses_commands_and_quoted_clients_are_not_facts(prompt):
    assert learning.confirmed_observation(command(prompt)) is None


@pytest.mark.parametrize('prompt', [
    'Проверили после подключения: причина была в интернете.',
    'Исправление: в этом кейсе связь восстановилась после перезапуска роутера.',
    'We verified the cause: router disconnected.',
    'Yoxladıq: internet bağlantısı kəsilib.',
])
def test_employee_confirmed_observations_and_corrections_are_recognized(prompt):
    assert learning.confirmed_observation(command(prompt))


def test_client_reply_mode_cannot_teach_even_with_confirmed_words():
    assert learning.confirmed_observation(command(mode='reply')) is None


def test_scoped_submission_denial_stops_without_fallback(monkeypatch):
    save = Mock(side_effect=TenantLinearError('Current private access denied', 403))
    monkeypatch.setattr('tenant_ai_agents_store.save_knowledge', save)
    result = learning.attempt(Mock(), Mock(), {'tenant_id': TENANT, 'telegram_id': 22}, command())
    assert result['status'] == 'denied'
    assert 'не сохранён' in result['message']
    save.assert_called_once()


def test_observation_uses_existing_inactive_agent_knowledge_with_dedup_provenance(monkeypatch):
    cur = Mock(); conn = Mock(); source_id = str(uuid.uuid4())
    monkeypatch.setattr('tenant_ai_agents_store._authorize', Mock(return_value=(TENANT, 22)))
    save = Mock(return_value={'id': source_id})
    monkeypatch.setattr('tenant_ai_agents_store.save_knowledge', save)
    cmd = command(); profile = {'tenant_id': TENANT, 'telegram_id': 22}
    first = learning.record(cur, conn, profile, cmd)
    second = learning.record(cur, conn, profile, cmd)
    assert first == second and first['status'] == 'pending_review'
    data = save.call_args.args[1]
    assert data['agent_id'] == AGENT and data['active'] is False
    assert save.call_args.kwargs['connection'] is conn
    assert save.call_args_list[0].args[1]['setup_key'] == data['setup_key']
    assert 'case only' in data['content'] and cmd['prompt'] in data['content']
    provenance = json.loads(cur.execute.call_args.args[1][0])
    assert provenance['employee_id'] == 22
    assert provenance['employee_turn_id'] == cmd['request_id']
    assert provenance['conversation_id'] == 'case-1'
    assert provenance['scope'] == 'single_case_employee_verified'
    assert provenance['review_status'] == 'pending_review'


def test_no_assigned_agent_is_blocked_and_context_lookup_is_tenant_route_scoped(monkeypatch):
    cur = Mock(); cur.fetchone.return_value = None
    cmd = command()
    selected = Mock(return_value={'agent_id':AGENT,'assignment_revision':1})
    monkeypatch.setattr('tenant_operator_case_submission.assignment', selected)
    with pytest.raises(TenantLinearError) as caught:
        copilot._assigned_context(cur, TENANT, cmd)
    assert caught.value.status == 409
    sql, args = cur.execute.call_args.args
    selected.assert_called_once_with(cur, TENANT, cmd['channel_id'], cmd['conversation_id'])
    assert args == (TENANT, AGENT)
    assert 'a.tenant_id=%s::uuid' in sql and 'a.archived_at IS NULL' in sql


def test_assignment_and_instruction_changes_invalidate_receipts():
    original = {'agent_id': AGENT, 'version': 1, 'instructions': 'support', 'company_instructions': 'company'}
    for change in ({'agent_id': str(uuid.uuid4())}, {'version': 2}, {'assignment_revision': 2},
                   {'instructions': 'updated'}, {'company_instructions': 'updated'}):
        assert copilot._agent_binding(original) != copilot._agent_binding({**original, **change})


def test_stale_assignment_hides_response_and_prevents_learning(monkeypatch):
    conn = MagicMock(); conn.__enter__.return_value = conn
    monkeypatch.setattr(copilot, '_connect', lambda: conn)
    monkeypatch.setattr(copilot, 'ensure_schema', Mock())
    monkeypatch.setattr(copilot, 'access', Mock(return_value=(TENANT, 22, SimpleNamespace(can_use_chat_ai=lambda: True))))
    monkeypatch.setattr(copilot, 'assert_route', Mock())
    monkeypatch.setattr(copilot, '_assigned_context', Mock(return_value={'agent_id': str(uuid.uuid4())}))
    attempt = Mock(); monkeypatch.setattr(learning, 'attempt', attempt)
    cmd = command(_agent_binding=copilot._agent_binding({'agent_id': AGENT}))
    with pytest.raises(TenantLinearError) as caught:
        copilot._finish(TENANT, cmd, 22, {'text': 'draft'}, [], cmd['prompt'], 'draft')
    assert caught.value.status == 409
    attempt.assert_not_called()
    conn.commit.assert_not_called()


def test_redaction_preserves_connection_diagnosis_and_removes_actual_secrets():
    value = learning.confirmed_observation(command('Проверили после подключения: причина в интернете.\nпароль: private-secret'))
    assert 'подключения: причина в интернете' in value
    assert 'private-secret' not in value


def test_duplicate_receipt_returns_cached_draft_without_generation_or_learning(monkeypatch):
    conn = MagicMock(); conn.__enter__.return_value = conn
    cur = conn.cursor.return_value.__enter__.return_value
    cmd = command(); public = {k: v for k, v in cmd.items() if not k.startswith('_')}
    public['provider'] = 'connect'
    context = {'agent_id': AGENT, 'version': 1}
    cleaned = copilot._clean(public)
    cleaned.update(_agent_id=AGENT, _agent_binding=copilot._agent_binding(context))
    cached = {'text': 'Customer-ready draft', 'learning': {'status': 'pending_review'}}
    cur.fetchone.side_effect = [
        {'secrets': b'cipher', 'metadata': {'enabled': True, 'consent': True, 'copilot_model': 'configured-model'}, 'updated_at': 'v1'},
        {'requested_by': 22, 'input_hash': copilot._hash(cleaned), 'status': 'completed', 'result': cached, 'config_version': 'v1'},
    ]
    monkeypatch.setattr(copilot, '_connect', lambda: conn)
    monkeypatch.setattr(copilot, 'ensure_schema', Mock())
    monkeypatch.setattr(copilot, 'access', Mock(return_value=(TENANT, 22, SimpleNamespace(can_use_chat_ai=lambda: True))))
    monkeypatch.setattr(copilot, 'assert_route', Mock(return_value={'lead_id': 17}))
    monkeypatch.setattr(copilot, '_assigned_context', Mock(return_value=context))
    monkeypatch.setattr('tenant_connect_identity.conflicted', Mock(return_value=False))
    generated = Mock(); recorded = Mock()
    monkeypatch.setattr(copilot, 'generate', generated)
    monkeypatch.setattr(learning, 'attempt', recorded)
    assert copilot.run({'tenant_id': TENANT, 'telegram_id': 22}, public) == cached
    generated.assert_not_called(); recorded.assert_not_called()
    assert not any('INSERT' in call.args[0] for call in cur.execute.call_args_list)


def test_support_operator_copilot_requires_existing_employee_ai_opt_in():
    policy = SimpleNamespace(can_use_chat_ai=lambda:False,
        allows=lambda permission:permission in {'support','dialogs'},member_settings={'ai_enabled':True})
    assert copilot._can_use_copilot(policy) is True
    policy.member_settings['ai_enabled'] = False
    assert copilot._can_use_copilot(policy) is False
    policy.member_settings['ai_enabled'] = True
    policy.allows = lambda permission:permission=='dialogs'
    assert copilot._can_use_copilot(policy) is False


@pytest.mark.parametrize('prompt', [
    'Мы не проверили причину; предполагаемый сбой сети.',
    'We have not confirmed diagnosis: router disconnected.',
    'Со слов клиента: проверили интернет.',
    'Проверили: предполагаемый сбой сети.',
    'Проверили: по словам клиента проблема в интернете.',
    'Напиши клиенту, что проверили интернет.',
    'Проверили: нет подтверждения причины.',
    'We confirmed: reportedly the router disconnected.',
    'We verified: customer says the router disconnected.',
    'Yoxlamadıq: internet problemi ola bilər.',
    'Yoxladıq: müştərinin sözlərinə görə internet problemi var.',
    'Təsdiqləməmişik: internet problemi.',
    'Düzəliş: bəlkə internet problemidir.',
    'Correction: unconfirmed router diagnosis.',
    'Исправление: причина неизвестно.',
])
def test_negations_hypotheses_and_attributed_reports_never_become_employee_confirmation(prompt):
    assert learning.confirmed_observation(command(prompt)) is None


@pytest.mark.parametrize('text,secrets', [
    ('Проверили причину. CVV: 123; срок карты: 12/29', ('123','12/29')),
    ('Проверили причину. CVC: 4321\nсрок действия карты: 10/30', ('4321','10/30')),
    ('Проверили причину. Пароли: abcDEF123', ('abcDEF123',)),
    ('Проверили причину. паролей: secret-example', ('secret-example',)),
    ('We verified the cause. passwords: fake-password', ('fake-password',)),
    ('Yoxladıq: internet. CVV2: 987\nexpiry date: 11/31', ('987','11/31')),
])
def test_private_teaching_redacts_short_payment_secrets_and_password_morphology(text,secrets):
    value = learning.confirmed_observation(command(text))
    assert value
    assert all(secret not in value for secret in secrets)
    assert 'SECRET' in value
