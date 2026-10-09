"""Bounded model-access recovery for legacy reply drafts, never message sending."""
import re
from typing import Protocol


class CompletionAPI(Protocol):
    def create(self, **kwargs: object) -> object: ...


class Log(Protocol):
    def warning(self, message: str, *args: object) -> None: ...
    def error(self, message: str, *args: object) -> None: ...


def candidates(preferred: str) -> list[str]:
    # These models all accept the existing text/image Chat Completions request.
    # Never turn a model error into a gateway call or an arbitrary model search.
    return list(dict.fromkeys([preferred, 'gpt-4.1', 'gpt-4o', 'gpt-4o-mini']))


def error_code(error: Exception) -> str:
    body = getattr(error, 'body', None)
    nested = body.get('error', body) if isinstance(body, dict) else {}
    code = getattr(error, 'code', None) or (nested.get('code') if isinstance(nested, dict) else None)
    return str(code or '')


def model_unavailable(error: Exception) -> bool:
    code = error_code(error)
    status = getattr(error, 'status_code', None)
    if code == 'model_not_found' and status in (None, 403, 404):
        return True
    # Compatibility with older SDK exceptions lacking a structured error body.
    return status in (403, 404) and not code and (
        'model_not_found' in str(error).lower() or 'model does not exist' in str(error).lower())


def safe_model(model: str) -> str:
    return model if re.fullmatch(r'gpt-[a-zA-Z0-9._-]{1,72}', model) else 'configured-model'


def log_failure(logger: Log, error: Exception) -> None:
    # Do not log exception strings: SDK bodies may echo API keys or input text.
    code = error_code(error)
    allowed = {'model_not_found', 'insufficient_quota', 'invalid_api_key', 'rate_limit_exceeded'}
    status = getattr(error, 'status_code', None)
    logger.error('Deal AI reply failed: status=%s code=%s',
                 status if isinstance(status, int) else 'unknown', code if code in allowed else 'provider_error')


def generate(api: CompletionAPI, preferred: str, messages: list[dict], logger: Log) -> tuple[str, str]:
    last_error: Exception | None = None
    for model in candidates(preferred):
        try:
            response = api.create(model=model, messages=messages, temperature=0.45, max_tokens=1000)
        except Exception as error:
            if not model_unavailable(error):
                raise
            logger.warning('Deal AI reply model unavailable: model=%s status=%s',
                           safe_model(model), getattr(error, 'status_code', None))
            last_error = error
            continue
        choices = getattr(response, 'choices', None)
        text = str((choices[0].message.content if choices else '') or '').strip()
        if model != preferred:
            logger.warning('Deal AI reply using compatible fallback: model=%s', safe_model(model))
        return text, model
    if last_error is not None:
        raise last_error
    raise RuntimeError('No reply model configured')
