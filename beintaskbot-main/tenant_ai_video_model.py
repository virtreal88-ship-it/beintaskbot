"""Separate owner-selected video vision model and fixed, capped call quote."""
import json
import math
from tenant_linear_policy import TenantLinearError

MODELS = ('gpt-4o', 'gpt-4o-mini', 'gpt-4.1', 'gpt-4.1-2025-04-14',
          'gpt-4.1-mini', 'gpt-4.1-nano')
MAX_FRAMES = 2
INPUT_UPPER = 2048
OUTPUT_UPPER = 600
PROVIDER_INPUT_OVERHEAD_UPPER = 256


def input_tokens_upper(model):
    """Conservative fixed-request input ceiling for the sampled video frames.

    Text is a fixed prompt plus a fixed JSON envelope, so its UTF-8 byte count
    is an upper bound on text tokens. The frame decoder caps every image at
    1280x720. Low-detail image token ceilings follow the model-specific image
    accounting rules; models whose image cost cannot fit the approved 2048
    input-token ceiling are rejected instead of being silently under-reserved.
    """
    from tenant_knowledge_media_cache import OBSERVATION_PROMPT
    envelope = json.dumps({'mode':'knowledge_visual_observation',
        'conversation':[],'manager_draft':''}, ensure_ascii=False)
    image_tokens = {
        'gpt-4.1': 85,
        'gpt-4.1-2025-04-14': 85,
        'gpt-4o': 85,
        # GPT-4.1-mini uses 32px patches at 1.62x; sampled frames are at
        # most 1280x720, yielding ceil(1280/32)*ceil(720/32)*1.62 = 1491.
        'gpt-4.1-mini': 1491,
        # GPT-4.1-nano patch multiplier is 2.46 for this snapshot.
        'gpt-4.1-nano': 2264,
        'gpt-4o-mini': 2833,
    }.get(model)
    if image_tokens is None:
        raise TenantLinearError('video_model_input_bound_unavailable', 409)
    text_bytes = len(OBSERVATION_PROMPT.encode('utf-8')) + len(envelope.encode('utf-8'))
    return text_bytes + image_tokens + PROVIDER_INPUT_OVERHEAD_UPPER


def require_input_bound(model):
    upper = input_tokens_upper(model)
    if upper > INPUT_UPPER:
        raise TenantLinearError('video_model_input_bound_exceeded', 409)
    return upper


def require_video_tariff(model):
    """Require the per-call reserve to cover the bounded token-priced request."""
    require_input_bound(model)
    from tenant_ai_spend_budget import price, media_price
    rates = price(model)
    unit = media_price(model, 'vision_low')
    if not rates or unit is None:
        raise TenantLinearError('video_model_tariff_required', 409)
    minimum = (math.ceil(INPUT_UPPER * rates[0] / 1_000_000) +
               math.ceil(OUTPUT_UPPER * rates[1] / 1_000_000))
    if unit < minimum:
        raise TenantLinearError('video_model_tariff_below_input_bound', 409)
    return {'unit_microusd':unit,'minimum_unit_microusd':minimum,
            'input_limit_tokens':INPUT_UPPER,'output_limit_tokens':OUTPUT_UPPER}


def quote(model, tenant_id):
    if model not in MODELS:
        raise TenantLinearError('Unsupported video model.', 400)
    from tenant_connect_images import supports_images
    if not supports_images(model):
        raise TenantLinearError('video_model_vision_not_allowed', 409)
    input_upper = require_input_bound(model)
    tariff = require_video_tariff(model)
    from tenant_ai_spend_budget import media_price, limit_microusd
    unit = media_price(model, 'vision_low')
    cap = limit_microusd(tenant_id)
    if unit is None:
        raise TenantLinearError('video_model_tariff_required', 409)
    if cap <= 0:
        raise TenantLinearError('video_model_capped_budget_required', 409)
    return {'model': model, 'max_frames': MAX_FRAMES, 'max_vision_calls': MAX_FRAMES,
            'input_upper_tokens': INPUT_UPPER, 'request_input_upper_tokens':input_upper,
            'output_upper_tokens': OUTPUT_UPPER,
            'minimum_vision_call_microusd':tariff['minimum_unit_microusd'],
            'max_microusd': unit * MAX_FRAMES, 'vision_call_microusd': unit,
            'budget_cap_configured': True}


def tariff_status(model, tenant_id):
    try:
        return {'ready': True, **quote(model, tenant_id)}
    except TenantLinearError as error:
        return {'ready': False, 'model': model, 'reason_code': str(error)}
