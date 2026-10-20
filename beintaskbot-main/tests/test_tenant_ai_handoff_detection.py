from tenant_ai_agent_actions import handoff_reason, should_handoff


def test_explicit_dissatisfaction_is_detected_in_az_ru_and_en():
    cases = [
        'Xidmətinizdən çox narazıyam, problem dəfələrlə yazmağıma baxmayaraq həll olunmayıb.',
        'Я уже несколько раз писал, проблема опять не решена.',
        "I'm frustrated with your support; this is still not fixed.",
        "I've contacted you multiple times and it's still not resolved.",
        'Botunuz eyni cavabı təkrarlayır, bu qəbuledilməzdir.',
        'Ваши ответы меня не устраивают, подам официальную жалобу.',
        "I demand that you resolve this immediately.",
    ]
    assert all(handoff_reason(text) == 'customer_dissatisfied' for text in cases)


def test_neutral_questions_and_polite_requests_do_not_handoff():
    cases = [
        'Salam, zəhmət olmasa sifarişimin vəziyyətini yoxlayın.',
        'Почему приложение просит обновить пароль?',
        'Can you please explain how to configure the router?',
        'Please solve my issue when you can.',
        "I'm frustrated with the traffic today.",
        'Менеджер будет доступен завтра?',
    ]
    assert all(handoff_reason(text) is None for text in cases)
    assert all(not should_handoff(text) for text in cases)


def test_quotes_and_reported_complaints_are_not_attributed_to_current_customer():
    cases = [
        'The customer said “your service is terrible”.',
        'Müştəri dedi: “xidmətinizdən narazıyam”.',
        'Клиент написал: «я недоволен обслуживанием».',
        'My colleague complained that your support was awful.',
        "The customer said 'your service is terrible'.",
    ]
    assert all(handoff_reason(text) is None for text in cases)


def test_direct_human_requests_keep_existing_reason_and_quoted_request_is_ignored():
    assert handoff_reason('Zəhmət olmasa, məni operatora qoşun.') == 'customer_requested_manager'
    assert handoff_reason('Can I please talk to a human?') == 'customer_requested_manager'
    assert handoff_reason('The user asked “Can I talk to a human?”') is None
