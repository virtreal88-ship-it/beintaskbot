"""Separate owner-selected video vision model and fixed, capped call quote."""
from tenant_linear_policy import TenantLinearError

MODELS = ('gpt-4o', 'gpt-4o-mini', 'gpt-4.1', 'gpt-4.1-2025-04-14',
          'gpt-4.1-mini', 'gpt-4.1-nano')
MAX_FRAMES = 2


def quote(model, tenant_id):
    if model not in MODELS:
        raise TenantLinearError('Unsupported video model.', 400)
    from tenant_connect_images import supports_images
    if not supports_images(model):
        raise TenantLinearError('video_model_vision_not_allowed', 409)
    from tenant_ai_spend_budget import media_price, limit_microusd
    unit = media_price(model, 'vision_low')
    cap = limit_microusd(tenant_id)
    if unit is None:
        raise TenantLinearError('video_model_tariff_required', 409)
    if cap <= 0:
        raise TenantLinearError('video_model_capped_budget_required', 409)
    return {'model': model, 'max_frames': MAX_FRAMES, 'max_vision_calls': MAX_FRAMES,
            'max_microusd': unit * MAX_FRAMES, 'vision_call_microusd': unit,
            'budget_cap_configured': True}


def tariff_status(model, tenant_id):
    try:
        return {'ready': True, **quote(model, tenant_id)}
    except TenantLinearError as error:
        return {'ready': False, 'model': model, 'reason_code': str(error)}
