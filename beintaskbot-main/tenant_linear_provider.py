"""Bounded tenant-key Linear adapter. Never falls back to a global API key."""
import logging
import re
import requests
from tenant_linear_policy import TenantLinearError


_LOG = logging.getLogger(__name__)
_CODES = frozenset({'UNAUTHENTICATED', 'FORBIDDEN', 'AUTHENTICATION_ERROR',
    'AUTHORIZATION_ERROR', 'RATELIMITED', 'RATE_LIMITED', 'GRAPHQL_VALIDATION_FAILED',
    'GRAPHQL_PARSE_FAILED', 'BAD_USER_INPUT', 'INTERNAL_SERVER_ERROR'})


def _failure(document, kind, status=0, codes=()):
    # Only locally defined labels enter logs. Never log request/response bodies,
    # variables, exception strings, credentials, or provider error messages.
    operation = next((name for name in ('issueUpdate', 'issueCreate', 'commentCreate',
        'viewer', 'teams', 'team', 'issues', 'issue', 'comment')
        if re.search(r'\b' + name + r'\s*[({]', document)), 'other')
    _LOG.warning('tenant_linear_failure operation=%s kind=%s http_status=%s codes=%s',
        operation, kind, status, ','.join(sorted(codes)) or 'none')
    if kind == 'authentication':
        message = 'Linear bağlantısının autentifikasiyası uğursuz oldu. Şirkətin Linear bağlantısını yoxlayın.'
    elif kind == 'permission':
        message = ('Linear bu dəyişikliyə icazə vermədi. Şirkətin Linear API açarında yazma icazəsini yoxlayın.'
            if document.lstrip().startswith('mutation') else
            'Linear bu məlumatlara girişə icazə vermədi. Şirkətin Linear bağlantısının icazələrini yoxlayın.')
    elif kind == 'rate_limit':
        message = 'Linear sorğu limiti dolub. Bir qədər sonra eyni sorğunu yenidən yoxlayın.'
    elif kind == 'network':
        message = 'Linear ilə əlaqə alınmadı. Əməliyyatın nəticəsi təsdiqlənmədi. Təkrar etməzdən əvvəl cari nəticəni yoxlayın.'
    else:
        message = 'Linear sorğunu tamamlaya bilmədi. Əməliyyatın nəticəsi təsdiqlənmədi. Dəstəyə müraciət edin.'
    raise TenantLinearError(message, 502) from None


def query(key, document, variables=None, *, timeout=(5, 20)):
    try:
        response = requests.post('https://api.linear.app/graphql',
            headers={'Authorization': key, 'Content-Type': 'application/json'},
            json={'query': document, 'variables': variables or {}}, timeout=timeout, allow_redirects=False)
    except requests.RequestException:
        _failure(document, 'network')
    status = response.status_code if isinstance(response.status_code, int) else 0
    try:
        payload = response.json()
    except (ValueError, requests.RequestException):
        payload = None
    errors = payload.get('errors') if isinstance(payload, dict) else None
    codes = set()
    if isinstance(errors, list):
        for error in errors:
            extensions = error.get('extensions') if isinstance(error, dict) else None
            code = extensions.get('code') if isinstance(extensions, dict) else None
            if isinstance(code, str) and code.upper() in _CODES:
                codes.add(code.upper())
    if status == 401 or codes & {'UNAUTHENTICATED', 'AUTHENTICATION_ERROR'}:
        _failure(document, 'authentication', status, codes)
    if status == 403 or codes & {'FORBIDDEN', 'AUTHORIZATION_ERROR'}:
        _failure(document, 'permission', status, codes)
    if status == 429 or codes & {'RATELIMITED', 'RATE_LIMITED'}:
        _failure(document, 'rate_limit', status, codes)
    if errors:
        _failure(document, 'graphql', status, codes)
    try:
        response.raise_for_status()
    except requests.RequestException:
        _failure(document, 'http', status, codes)
    if status and not 200 <= status < 300:
        _failure(document, 'http', status, codes)
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict):
        _failure(document, 'invalid_response', status, codes)
    return payload['data']


def verify(key):
    if not isinstance(key, str) or not 10 <= len(key.strip()) <= 512 or any(ord(c) < 33 for c in key.strip()):
        raise TenantLinearError('Linear API açarı düzgün deyil.')
    data = query(key.strip(), 'query { viewer { id } }')
    if not isinstance(data.get('viewer'), dict) or not data['viewer'].get('id'):
        raise TenantLinearError('Linear hesabı təsdiqlənmədi.', 502)


def catalog(key, after=None):
    # One explicit page; no full workspace loads and no shared cross-tenant cache.
    return query(key, '''query($after:String) {
      teams(first:20,after:$after) { nodes { id name key } pageInfo { hasNextPage endCursor } }
    }''', {'after': after})['teams']


def team_catalog(key, team_id):
    return query(key, '''query($id:String!) { team(id:$id) { id name
      states(first:50) { nodes { id name type position } pageInfo { hasNextPage } }
      projects(first:50) { nodes { id name } pageInfo { hasNextPage } }
    } }''', {'id': team_id}).get('team')
