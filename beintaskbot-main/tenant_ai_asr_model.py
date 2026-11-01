"""Bounded per-call ASR quotes for manual and case media processing.

The reserve is pinned before dispatch. Models without an enforceable upper
bound are not admitted to paid case-media extraction.
"""
import math

from tenant_linear_policy import TenantLinearError

CANONICAL_MODEL = 'gpt-transcribe'
MODELS = (CANONICAL_MODEL, 'gpt-4o-mini-transcribe', 'whisper-1')
DEFAULT_RESERVE_MICROUSD = 30_000
# Official gpt-transcribe duration tariff (2026-11-01): $0.0045/minute.
# https://developers.openai.com/api/docs/models/gpt-transcribe
TRANSCRIBE_MICROUSD_PER_MINUTE = 4_500
VIDEO_MAX_SECONDS = 120
AUDIO_MAX_SECONDS = 300
# https://developers.openai.com/api/docs/models/whisper-1 (2026-11-01):
# $0.006/minute. Cover the maximum 300s enforced by validate_audio, not
# merely the shorter 120s video extraction limit.
WHISPER_MICROUSD_PER_MINUTE = 6_000
# OpenAI model documentation (checked 2026-10-28) gives gpt-4o-mini-transcribe
# a 16k context, 2k max output, and audio-token rates of $1.25/$5 per million.
# Keep the rate with the enforced model bound so the operation reserve cannot
# silently inherit an unrelated text model's tariff.
TOKEN_BOUNDS = {'gpt-4o-mini-transcribe': (16_000, 2_000, 1_250_000, 5_000_000)}


def minimum_reserve_microusd(model):
    if model == CANONICAL_MODEL:
        return math.ceil(AUDIO_MAX_SECONDS * TRANSCRIBE_MICROUSD_PER_MINUTE / 60)
    if model == 'whisper-1':
        return math.ceil(AUDIO_MAX_SECONDS * WHISPER_MICROUSD_PER_MINUTE / 60)
    bounds = TOKEN_BOUNDS.get(model)
    if bounds is None:
        raise TenantLinearError(f'asr_cost_bound_unavailable: model={model} operation=asr', 409)
    input_limit, output_limit, input_rate, output_rate = bounds
    return math.ceil(input_limit * input_rate / 1_000_000) + math.ceil(output_limit * output_rate / 1_000_000)


def quote(model):
    if model not in MODELS:
        raise TenantLinearError(f'asr_cost_bound_unavailable: model={model} operation=asr', 409)
    minimum = minimum_reserve_microusd(model)
    from tenant_ai_spend_budget import media_price
    configured = media_price(model, 'asr')
    if configured is None:
        raise TenantLinearError(f'asr_media_tariff_required: model={model} operation=asr minimum_microusd={minimum}', 409)
    if configured < minimum:
        raise TenantLinearError(f'asr_media_tariff_below_bound: model={model} operation=asr minimum_microusd={minimum}', 409)
    if model == CANONICAL_MODEL and configured > DEFAULT_RESERVE_MICROUSD:
        raise TenantLinearError('asr_media_tariff_exceeds_authorized_bound', 409)
    return {'model': model, 'operation': 'asr', 'ready': True,
            'reserve_microusd': configured, 'minimum_microusd': minimum,
            'max_input_tokens': TOKEN_BOUNDS.get(model, (None, None))[0],
            'max_output_tokens': TOKEN_BOUNDS.get(model, (None, None))[1],
            'max_duration_seconds': AUDIO_MAX_SECONDS if model in {'whisper-1', CANONICAL_MODEL} else VIDEO_MAX_SECONDS}


def tariff_status(model):
    try:
        return quote(model)
    except TenantLinearError as error:
        return {'model': model, 'operation': 'asr', 'ready': False,
                'reason_code': str(error)}


def effective_model(configured, *, platform=False):
    """Resolve internal legacy defaults without changing saved company choices."""
    value = str(configured or '').strip()
    if value == 'whisper-1' or (platform and value == 'gpt-4o-mini-transcribe'):
        return CANONICAL_MODEL
    if value not in MODELS:
        error = TenantLinearError('ai_spend_media_price_unknown', 409)
        from tenant_media_admission import mark
        mark(error, 'budget')
        raise error
    return value


def request_policy(model):
    # Auto-detect multilingual speech. Agent response language is not an audio hint.
    return {'model': model, 'response_format': 'json', 'language_policy': 'auto'}
