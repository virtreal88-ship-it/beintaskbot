"""The language decision uses selected customer facts and no provider calls."""
import pytest

from tenant_summary_language import resolve, instructions, language, LANGUAGES
from tenant_linear_policy import TenantLinearError

POLICY = {'mode': 'automatic', 'fallback_language': 'az', 'version': 7, 'source': 'company'}


def customer(text, **extra):
    return {'role': 'customer', 'text': text, **extra}


@pytest.mark.parametrize(('texts', 'expected'), [
    (['Salam, proqramda mehsul silinmir, xeta gosterir. Zehmet olmasa komek edin.'], 'az'),
    (['Salam, proqramda məhsul silinmir. Xəta göstərir, zəhmət olmasa kömək edin.'], 'az'),
    (['Здравствуйте, после сохранения накладной возникает ошибка, помогите решить проблему.'], 'ru'),
    (['Программа сломалась'], 'ru'),
    (['Ошибка обновления'], 'ru'),
    (['Program açılmıyor'], 'tr'),
    (['Save fails'], 'en'),
    (['Hello, we cannot save the product invoice. Please help fix this error.'], 'en'),
    (['Merhaba, programda urunu kaydedemiyorum. Lutfen bu sorunu duzeltmek icin yardim eder misiniz?'], 'tr'),
    (['Salam, proqramda mehsul silinmir, xeta gosterir. Zehmet olmasa komek edin.', 'ok'], 'az'),
    (['Salam, proqramda mehsul silinmir, xeta gosterir. Zehmet olmasa komek edin.',
      'спасибо всем большое', 'спасибо очень помогли'], 'az'),
    (['Salam, proqramda mehsul silinmir, xeta gosterir. Zehmet olmasa komek edin.',
      'да спасибо', 'ок спасибо'], 'az'),
    (['SKU AB123 https://example.test/ru?product=1', '👍', 'ok'], 'az'),
    (['Программа не сохраняет товар и показывает ошибку.', 'Please answer in English.'], 'en'),
    (['Please answer in English.', 'Пожалуйста, пишите на русском.'], 'ru'),
    (['Please help with this problem and save our invoice.',
      'Программа перестала сохранять товары, просим проверить ошибку.',
      'Теперь не открывается карточка товара, помогите это исправить.'], 'ru'),
])
def test_customer_language_matrix(texts, expected):
    result = resolve(POLICY, [customer(text) for text in texts])
    assert result['language'] == expected
    assert result['policy_version'] == 7 and result['detector_version'] == 1
    assert LANGUAGES[expected] in instructions(result)


def test_ui_locale_staff_and_developer_language_cannot_override_customer():
    rows = [customer('Hello, we cannot save the product invoice. Please help fix this error.'),
            {'role': 'operator', 'text': 'Русский ответ разработчика. ' * 70},
            {'direction': 'outgoing', 'body': 'Türkçe konuşun lütfen. ' * 70},
            {'direction': 'incoming', 'raw': {'verified_employee_id': 11}, 'body': 'Пишите на русском.'}]
    assert resolve(POLICY, rows)['language'] == 'en'
    assert resolve(POLICY, [{'text': 'Пожалуйста, пишите на русском.'}])['method'] == 'fallback'


def test_complete_saved_voice_text_is_customer_evidence_without_new_transcription():
    rows = [customer('', message_type='voice', transcript='Salam, proqramda mehsul silinmir, zehmet olmasa komek edin.')]
    assert resolve(POLICY, rows)['language'] == 'az'
    rows[0]['transcript'] = ''
    rows[0]['body'] = 'This body is not a verified transcript; it must not be used.'
    assert resolve(POLICY, rows)['method'] == 'fallback'


def test_priority_and_compatibility_are_explicit():
    rows = [customer('Hello, we cannot save the product invoice. Please help fix this error.')]
    fixed = {**POLICY, 'mode': 'fixed', 'fixed_language': 'ru'}
    assert resolve(fixed, rows)['language'] == 'ru'
    assert resolve(fixed, rows, explicit='tr')['method'] == 'explicit'
    assert resolve(fixed, rows, explicit='tr')['language'] == 'tr'
    legacy = {**POLICY, 'mode': 'legacy', 'version': 0, 'source': 'legacy'}
    assert resolve(legacy, rows, legacy_language='az')['language'] == 'az'
    assert resolve(legacy, rows, legacy_language='ru')['language'] == 'ru'
    assert resolve(legacy, rows, legacy_language='auto')['language'] == 'en'


def test_unsupported_language_is_rejected_before_paid_work():
    with pytest.raises(TenantLinearError):
        language('de')


@pytest.mark.parametrize('text',["It doesn't work",'Cannot login','I cannot print',"I can’t print","Doesn't work","Don't print"])
def test_short_english_technical_message_is_substantive_customer_evidence(text):
    selected=resolve(POLICY,[customer(text)])
    assert selected['language']=='en' and selected['method']=='customer_detected'


@pytest.mark.parametrize('text',['Thanks, it works now','Thank you for your help','Thank you, this works','This works',
    'Microsoft Print','Workday Print','ERR_PRINT_FAILED','ERR-PRINT-FAILED','cannot_login','SKU AB123 ERR104'])
def test_acknowledgement_product_or_code_does_not_become_a_new_language_phase(text):
    selected=resolve(POLICY,[customer(text),customer(text)])
    assert selected['language']=='az' and selected['method']=='fallback'
