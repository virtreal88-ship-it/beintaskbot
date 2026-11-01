"""Bounded per-call ASR quotes for manual and case media processing.

The reserve is pinned before dispatch. Models without an enforceable upper
bound are not admitted to paid case-media extraction.
"""
import math

from tenant_linear_policy import TenantLinearError

MODELS = ('gpt-4o-mini-transcribe', 'whisper-1')
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
    return {'model': model, 'operation': 'asr', 'ready': True,
            'reserve_microusd': configured, 'minimum_microusd': minimum,
            'max_input_tokens': TOKEN_BOUNDS.get(model, (None, None))[0],
            'max_output_tokens': TOKEN_BOUNDS.get(model, (None, None))[1],
            'max_duration_seconds': AUDIO_MAX_SECONDS if model == 'whisper-1' else VIDEO_MAX_SECONDS}


def tariff_status(model):
    try:
        return quote(model)
    except TenantLinearError as error:
        return {'model': model, 'operation': 'asr', 'ready': False,
                'reason_code': str(error)}


# Internal standalone-dialog rollout; unrelated to any experiment authorization.
# Tenant identifiers and production message/reference text are not published.
SHARED_ASR_TENANT_DIGEST = '18bf0f79cb1e3b250dba8c4fd881b5ddc43e79be87cabab9ef217d0bf44f5f9d'
STRONG_MODEL = 'gpt-4o-transcribe'
SHARED_ASR_CONTEXTS = {'manual_helper', 'connect_auto'}


def shared_asr_tenant(tenant):
    import hashlib
    import uuid
    try:
        canonical = str(uuid.UUID(str(tenant)))
    except (ValueError, TypeError, AttributeError):
        return False
    return hashlib.sha256(('shared-dialog-asr-rollout:v1:' + canonical).encode()).hexdigest() == SHARED_ASR_TENANT_DIGEST


def scoped_strong_quote(tenant):
    """Private exact-6c quote; public model registry/admission stays unchanged."""
    import json
    import os
    if not shared_asr_tenant(tenant):
        raise TenantLinearError('shared_asr_scope_unavailable', 409)
    # Never turn a previously blocked/repriced Whisper operation into a new call.
    old = quote('whisper-1')
    if old['reserve_microusd'] != 30_000:
        raise TenantLinearError('shared_asr_existing_tariff_changed', 409)
    try:
        registry = json.loads(os.environ.get('AI_MEDIA_OPERATION_PRICES_MICROUSD') or '{}')
        if not isinstance(registry, dict):
            raise ValueError()
        entry = registry.get(STRONG_MODEL, {})
        if not isinstance(entry, dict):
            raise ValueError()
        amount = entry.get('asr', 60_000)
        if (isinstance(amount, bool) or not isinstance(amount, (int, str))
                or (isinstance(amount, str) and not amount.isdecimal()) or int(amount) != 60_000):
            raise ValueError()
    except (ValueError, TypeError):
        raise TenantLinearError('shared_asr_tariff_unavailable', 409) from None
    # https://developers.openai.com/api/docs/models/gpt-4o-transcribe
    # 16k input at $2.5/M + 2k output at $10/M = 60000 microUSD.
    # Owner-approved fixed reserve/settlement; legacy public tariffs stay unchanged.
    return {'model': STRONG_MODEL, 'operation': 'asr', 'reserve_microusd': 60_000,
            'minimum_microusd': 60_000, 'max_input_tokens': 16_000, 'max_output_tokens': 2_000, 'max_duration_seconds': AUDIO_MAX_SECONDS}


def shared_audio_model(configured, *, tenant, credential_source, context):
    """Opt in only the authorized company/shared standalone-audio execution."""
    value = str(configured or '').strip()
    if (credential_source != 'company' or context not in SHARED_ASR_CONTEXTS
            or value != 'whisper-1' or not shared_asr_tenant(tenant)):
        return value
    try:
        scoped_strong_quote(tenant)
    except TenantLinearError:
        return value
    return STRONG_MODEL


def shared_request_policy():
    return {'model': STRONG_MODEL, 'response_format': 'json', 'language_policy': 'auto'}
