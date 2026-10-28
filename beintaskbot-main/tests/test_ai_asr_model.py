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


def test_whisper_reserves_full_bounded_video_duration(monkeypatch):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD', json.dumps({'whisper-1': {'asr': 12_000}}))
    assert asr.minimum_reserve_microusd('whisper-1') == 12_000
    assert asr.quote('whisper-1')['max_duration_seconds'] == 120


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
