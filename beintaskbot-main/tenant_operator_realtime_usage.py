"""Strict, version-pinned Realtime usage accounting.

Only the trusted server-side `response.done` payload may be passed here. Any
missing or inconsistent modality counts return None so the caller retains its
full reservation instead of guessing a charge.
"""
MODEL = 'gpt-realtime-2.1-mini'
QUOTE = 'realtime-2.1-mini-gpt-transcribe-2026-11-03-v1'
MICRO = 1_000_000

# USD per million tokens, represented as integer micro-USD per million tokens.
RATES = {
    'input_text': 600_000,
    'input_text_cached': 60_000,
    'input_audio': 10_000_000,
    'input_audio_cached': 300_000,
    'output_text': 2_400_000,
    'output_audio': 20_000_000,
}


def _count(value):
    return value if type(value) is int and 0 <= value < 2**63 else None


def _cost(tokens, rate):
    return (tokens * rate + MICRO - 1) // MICRO


def usage_cost_microusd(usage):
    """Return a conservative rounded-up charge, or None for uncertain usage."""
    if not isinstance(usage, dict):
        return None
    total_in = _count(usage.get('input_tokens'))
    total_out = _count(usage.get('output_tokens'))
    total = _count(usage.get('total_tokens'))
    incoming = usage.get('input_token_details')
    outgoing = usage.get('output_token_details')
    if (None in (total_in, total_out, total) or total_in + total_out != total or
            not isinstance(incoming, dict) or not isinstance(outgoing, dict)):
        return None

    in_text = _count(incoming.get('text_tokens', 0))
    in_audio = _count(incoming.get('audio_tokens', 0))
    in_image = _count(incoming.get('image_tokens', 0))
    out_text = _count(outgoing.get('text_tokens', 0))
    out_audio = _count(outgoing.get('audio_tokens', 0))
    if None in (in_text, in_audio, in_image, out_text, out_audio):
        return None
    if in_text + in_audio + in_image != total_in or out_text + out_audio != total_out:
        return None

    cached_details = incoming.get('cached_tokens_details') or {}
    if not isinstance(cached_details, dict):
        return None
    cached_text = _count(cached_details.get('text_tokens', 0))
    cached_audio = _count(cached_details.get('audio_tokens', 0))
    cached_image = _count(cached_details.get('image_tokens', 0))
    cached_total = _count(incoming.get('cached_tokens', 0))
    if (None in (cached_text, cached_audio, cached_image, cached_total) or
            cached_text > in_text or cached_audio > in_audio or cached_image > in_image or
            cached_text + cached_audio + cached_image != cached_total):
        return None
    # Images and any future/unknown modality cannot be priced by this quote.
    # Image usage is forbidden by the server session configuration.
    if in_image or cached_image:
        return None
    return sum((
        _cost(in_text - cached_text, RATES['input_text']),
        _cost(cached_text, RATES['input_text_cached']),
        _cost(in_audio - cached_audio, RATES['input_audio']),
        _cost(cached_audio, RATES['input_audio_cached']),
        _cost(out_text, RATES['output_text']),
        _cost(out_audio, RATES['output_audio']),
    ))
