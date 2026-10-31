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


def test_real_case_quote_does_not_authorize_unrelated_registration_or_diagnosis_claim():
    lines = [{'speaker': 'CLIENT', 'text': 'Alış 1 qutu 50 AZN'}]
    false_delivery_claim = 'Müraciət qeydə alındı və proqramçılara göndərildi.'
    assert parse(decision('reply', false_delivery_claim, 'Alış 1 qutu 50 AZN'),
        lines=lines, knowledge=[]) is None
    # Even when the requested evidence excerpt is real, the reply itself must
    # be grounded, so a separate bug article cannot substantiate this claim.
    knowledge = [{'source_id': 'known-bug', 'content': 'A rounding issue can display 49.9992.'}]
    assert parse(decision('reply', 'The shown value proves this rounding bug is the cause.',
        'A rounding issue can display 49.9992.'), lines=lines, knowledge=knowledge) is None


def test_similar_kb_quote_cannot_be_repeated_as_a_diagnosis_for_this_case():
    lines = [{'speaker': 'CLIENT', 'text': 'The total is 50 AZN but shown as 49.9992.'}]
    kb_text = 'The mismatch is caused by a rounding defect.'
    assert parse(decision('reply', kb_text, kb_text), lines=lines,
        knowledge=[{'source_id': 'similar-case', 'content': kb_text}]) is None


def test_clarification_discards_model_authored_delivery_claim_and_renders_safe_question():
    lines = [{'speaker': 'CLIENT', 'text': 'Alış 1 qutu 50 AZN, ekranda 49,9992 göstərilir.'}]
    result = parse(decision('clarify', 'Müraciətinizi proqramçılara ötürdüm. Hansı versiyadan istifadə edirsiniz?',
        missing_items=['version']), lines=lines, knowledge=[], language='az')
    assert result and result['action'] == 'clarify'
    assert result['message'] == 'Zəhmət olmasa, istifadə etdiyiniz proqramın versiyasını yaza bilərsiniz?'
    assert 'ötürdüm' not in result['message']


def test_supplied_version_is_not_requested_again():
    lines = [{'speaker': 'CLIENT', 'text': 'I use app v2.4.1 and see the wrong total.'}]
    assert parse(decision('clarify', 'Müraciətinizi proqramçılara ötürdüm. Hansı versiyadan istifadə edirsiniz?',
        missing_items=['version']), lines=lines, knowledge=[], language='az') is None


def test_supplied_document_detail_cannot_be_requested_again():
    lines = [{'speaker': 'CLIENT', 'text': 'Alış 1 qutu 50 AZN, qaimədə göstərilib.'}]
    assert parse(decision('clarify', 'Hansı alış sənədidir?', missing_items=['Alış']),
        lines=lines, knowledge=[]) is None


@pytest.mark.parametrize('missing', ['sənəd', 'qaimə', 'invoice', 'alış', 'məhsul', 'məbləğ', 'amount'])
def test_document_product_and_amount_families_are_not_requested_again(missing):
    lines = [{'speaker': 'CLIENT', 'text': 'Alış 1 qutu 50 AZN, qaimədə göstərilib.'}]
    result = parse(decision('clarify', 'Could you clarify the missing detail?', missing_items=[missing]),
        lines=lines, knowledge=[])
    assert result is None


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
        'message': 'Please share an interface screenshot showing the issue or a short video reproducing it.', 'evidence_quote': '',
        'missing_items': [], 'request_media': True}


def test_existing_media_cannot_be_requested_again():
    assert parse(decision('clarify', 'Please share a screenshot.', request_media=True),
        lines=[{'speaker': 'CLIENT', 'text': 'See attached.'}], knowledge=[], media_usable=True,
        media_present=True) is None


def test_unreadable_existing_image_allows_only_targeted_better_media_request():
    lines=[{'speaker':'CLIENT','text':'See attached.'}]
    assert parse(decision('clarify','Please share a screenshot.',request_media=True),
        lines=lines,knowledge=[],media_present=True) is None
    result = parse(decision('clarify','Please share a clearer screenshot of the amount.',request_media=True),
        lines=lines,knowledge=[],media_present=True)
    assert result and result['message'] == 'Please share a clearer screenshot of that screen or a short video reproducing the issue.'


def test_initial_prompt_forbids_false_registration_and_resolution_claims():
    prompt = instructions().casefold()
    for claim in ('registered', 'escalated', 'sent', 'fixed', 'resolved'):
        assert claim in prompt
    assert 'never proof' in prompt
