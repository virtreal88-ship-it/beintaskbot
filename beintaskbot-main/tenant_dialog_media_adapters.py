"""Internal attachment adapters for one assigned agent, never another agent.

The agent's selected model reasons about the dialog. Use its image capability
when available. File ASR and video decoding retain the already-configured
technical processors and existing consent/tariff gates; do not guess a model or
introduce selectable per-agent ASR/Vision settings.
"""
from tenant_connect_images import supports_images


def configuration(agent_model, company_config):
    result = dict(company_config)
    # An image-capable selected model owns ordinary photo understanding too.
    # Otherwise the existing photo processor supplies untrusted observations
    # to this same agent. A missing processor remains unavailable.
    if supports_images(str(agent_model or '')):
        result['photo_model'] = str(agent_model)
    return result
