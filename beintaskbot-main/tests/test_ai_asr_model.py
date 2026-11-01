"""Fixed upper-bound ASR quotes; no provider calls."""
import json

import pytest

from tenant_linear_policy import TenantLinearError
import tenant_ai_asr_model as asr


def test_gpt4o_mini_transcribe_bound_uses_model_context_and_max_output(monkeypatch):
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION', json.dumps({
        'gpt-4o-mini-transcribe': {'input': 1_250_000, 'output': 5_000_000}}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD', json.dumps({
        'gpt-4o-mini-transcribe': {'asr': 30_000}}))
    assert asr.minimum_reserve_microusd('gpt-4o-mini-transcribe') == 30_000
    assert asr.quote('gpt-4o-mini-transcribe') == {
        'model': 'gpt-4o-mini-transcribe', 'operation': 'asr', 'ready': True,
        'reserve_microusd': 30_000, 'minimum_microusd': 30_000,
        'max_input_tokens': 16_000, 'max_output_tokens': 2_000,
        'max_duration_seconds': 120}


@pytest.mark.parametrize('media,reason', [
    ({}, 'asr_media_tariff_required'),
    ({'asr': 29_999}, 'asr_media_tariff_below_bound'),
])
def test_gpt4o_mini_transcribe_fails_closed_without_exact_bound(monkeypatch, media, reason):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD', json.dumps(
        {'gpt-4o-mini-transcribe': media} if media else {}))
    with pytest.raises(TenantLinearError, match=reason):
        asr.quote('gpt-4o-mini-transcribe')


def test_whisper_reserves_full_bounded_manual_audio_duration(monkeypatch):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD', json.dumps({'whisper-1': {'asr': 30_000}}))
    assert asr.minimum_reserve_microusd('whisper-1') == 30_000
    assert asr.quote('whisper-1')['max_duration_seconds'] == 300


def test_unbounded_asr_model_is_not_admitted_even_with_arbitrary_positive_rate(monkeypatch):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD', json.dumps({'other-model': {'asr': 999_999}}))
    with pytest.raises(TenantLinearError, match='asr_cost_bound_unavailable: model=other-model operation=asr'):
        asr.quote('other-model')


def test_owner_settings_readback_reports_asr_readiness_separately(monkeypatch):
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION', '{}')
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD', '{}')
    from tenant_chat_ai_settings import public
    result=public({'secrets':b'encrypted-placeholder','metadata':{}},'tenant-id')
    mini=result['asr_model_tariffs']['gpt-4o-mini-transcribe']
    assert mini['ready'] is False
    assert mini['reason_code']=='asr_media_tariff_required: model=gpt-4o-mini-transcribe operation=asr minimum_microusd=30000'


def test_missing_whisper_tariff_uses_official_full_duration_bound_without_env_mutation(monkeypatch):
    from tenant_ai_spend_budget import media_price
    configured=json.dumps({'gpt-4o-mini-transcribe':{'asr':30000},'gpt-4.1':{'vision_low':8896}})
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',configured)
    assert media_price('whisper-1','asr')==30000
    quote=asr.quote('whisper-1')
    assert quote['ready'] and quote['minimum_microusd']==30000 and quote['max_duration_seconds']==300
    assert media_price('gpt-4o-mini-transcribe','asr')==30000
    assert media_price('gpt-4.1','vision_low')==8896
    assert media_price('unpriced-asr','asr') is None
    assert media_price('whisper-1','vision_low') is None
    import os
    assert os.environ['AI_MEDIA_OPERATION_PRICES_MICROUSD']==configured


@pytest.mark.parametrize('value',[0,-1,None,'disabled'])
def test_explicit_invalid_whisper_tariff_is_not_bypassed(monkeypatch,value):
    from tenant_ai_spend_budget import media_price
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'whisper-1':{'asr':value}}))
    assert media_price('whisper-1','asr') is None


@pytest.mark.parametrize('raw',['not-json','[]','{"whisper-1":null}'])
def test_malformed_registry_is_not_replaced_by_fallback(monkeypatch,raw):
    from tenant_ai_spend_budget import media_price
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',raw)
    assert media_price('whisper-1','asr') is None


def test_explicit_whisper_reserve_is_preserved_and_underbound_is_rejected_by_quote(monkeypatch):
    from tenant_ai_spend_budget import media_price
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{"whisper-1":{"asr":40000}}')
    assert media_price('whisper-1','asr')==40000
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{"whisper-1":{"asr":12000}}')
    with pytest.raises(TenantLinearError,match='asr_media_tariff_below_bound'):asr.quote('whisper-1')
