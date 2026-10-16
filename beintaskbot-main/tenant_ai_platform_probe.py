"""One-shot, opt-in verification of centrally proposed OpenAI model IDs."""
import json
import os

from tenant_chat_ai_provider import ChatAIProviderError, generate


def run_configured_probe(logger):
    """Make at most one minimal request per explicitly configured candidate.

    It is disabled unless a platform administrator sets the Railway flag.  No
    request, response text or credential is logged; deployment logs contain
    only a model identifier and a safe availability category.
    """
    if str(os.environ.get('AI_AGENTS_PLATFORM_MODEL_PROBE') or '').lower() not in {'1', 'true', 'yes'}:
        return
    key = str(os.environ.get('OPENAI_API_KEY') or '')
    try:
        candidates = json.loads(str(os.environ.get('AI_AGENTS_PLATFORM_MODEL_PROBE_CANDIDATES') or '[]'))
    except ValueError:
        candidates = []
    if not key or not isinstance(candidates, list):
        logger.warning('AI platform model probe skipped: invalid platform configuration')
        return
    for item in list(dict.fromkeys(str(value).strip() for value in candidates if isinstance(value, str)))[:12]:
        if not item or len(item) > 100:
            continue
        try:
            generate(key, item, 'probe', [], '', instructions='Reply with OK.', with_usage=True)
            logger.info('AI platform model probe: model=%s availability=available', item)
        except ChatAIProviderError as error:
            logger.info('AI platform model probe: model=%s availability=unavailable category=%s status=%s',
                        item, error.category, error.status_code)
        except Exception:
            logger.info('AI platform model probe: model=%s availability=unavailable category=unknown', item)
