import json

import pytest

from tenant_ai_pilot_initial import instructions, parse


def decision(action, message, quote='', missing_items=None, request_media=False):
    return json.dumps({'action': action, 'message': message, 'evidence_quote': quote,
        'missing_items': missing_items or [], 'request_media': request_media})


def test_similar_knowledge_issue_cannot_establish_customer_case_cause():
    lines = [{'speaker': 'CLIENT', 'text': 'Alış 1 qutu 50 AZN, ekranda 49,9992 göstərilir.'}]
    knowledge = [{'source_id': 'known-bug', 'content': 'A rounding bug may display 49.9992.'}]
    # A KB excerpt can ground a customer explanation, but cannot justify
    # forwarding as though the current case's cause were already established.
    assert parse(decision('consult_developer', 'We found the cause.',
        'A rounding bug may display 49.9992.'), lines=lines, knowledge=knowledge) is None
    assert parse(decision('consult_developer', 'Please investigate this report.',
        'Alış 1 qutu 50 AZN'), lines=lines, knowledge=knowledge)
    assert parse(decision('consult_developer', 'This is our fault; we found the cause.',
        'Alış 1 qutu 50 AZN'), lines=lines, knowledge=knowledge) is None


def test_supplied_document_detail_cannot_be_requested_again():
    lines = [{'speaker': 'CLIENT', 'text': 'Alış 1 qutu 50 AZN, qaimədə göstərilib.'}]
    assert parse(decision('clarify', 'Hansı alış sənədidir?', missing_items=['Alış']),
        lines=lines, knowledge=[]) is None


def test_existing_document_details_and_media_are_explicitly_protected_from_repeat_requests():
    prompt = instructions().casefold()
    assert 'do not ask again for details already present' in prompt
    assert 'do not request it again' in prompt
    assert 'screenshot or short reproduction video' in prompt


@pytest.mark.parametrize('action,quote', [
    ('clarify', 'not empty'),
    ('consult_developer', ''),
    ('consult_developer', 'text that is not in the case'),
])
def test_initial_route_rejects_bad_or_unverifiable_evidence(action, quote):
    lines = [{'speaker': 'CLIENT', 'text': 'The total is 50 AZN but shown as 49.9992.'}]
    assert parse(decision(action, 'Please share a screenshot.', quote),
        lines=lines, knowledge=[]) is None


def test_initial_clarification_is_structured_and_does_not_require_evidence_claim():
    result = parse(decision('clarify', 'Could you share a screenshot of the displayed amount?',
        request_media=True), lines=[{'speaker': 'CLIENT', 'text': 'It looks wrong.'}], knowledge=[])
    assert result == {'action': 'clarify',
        'message': 'Could you share a screenshot of the displayed amount?', 'evidence_quote': '',
        'missing_items': [], 'request_media': True}


def test_existing_media_cannot_be_requested_again():
    assert parse(decision('clarify', 'Please share a screenshot.', request_media=True),
        lines=[{'speaker': 'CLIENT', 'text': 'See attached.'}], knowledge=[], media_available=True) is None


def test_initial_prompt_forbids_false_registration_and_resolution_claims():
    prompt = instructions().casefold()
    for claim in ('registered', 'escalated', 'sent', 'fixed', 'resolved'):
        assert claim in prompt
    assert 'never proof' in prompt
