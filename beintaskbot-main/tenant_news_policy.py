"""Publication validation: short plain text, explicit URL, no automatic defaults."""
import base64
import json
import re
from urllib.parse import urlsplit
from tenant_linear_policy import identifier, TenantLinearError
from tenant_linear_observer_policy import source_time


def fields(data: dict) -> dict:
    result = {}
    for key, limit in (('title', 255), ('summary', 1200), ('project_name', 100), ('url', 2048)):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > limit or '\x00' in value:
            raise TenantLinearError('Xəbərin sahələri düzgün deyil.')
        result[key] = value.strip()
    if not all(result[key] for key in ('title', 'summary', 'project_name')):
        raise TenantLinearError('Başlıq, qısa mətn və layihə məcburidir.')
    link(result['url'])
    return result


def link(value: str) -> str:
    if not value:
        return ''
    if re.fullmatch(r'\+?[0-9 ()-]{7,25}', value) and 7 <= len(re.sub(r'\D', '', value)) <= 20:
        return 'tel:' + re.sub(r'[^+0-9]', '', value)
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError:
        raise TenantLinearError('URL düzgün deyil.') from None
    if (parsed.scheme in {'https', 'http'} and parsed.hostname and not parsed.username
            and not parsed.password and not any(c.isspace() or ord(c) < 32 for c in value)):
        return value
    if re.fullmatch(r'tel:\+?[0-9]{7,20}', value):
        return value
    raise TenantLinearError('URL https://, http:// və ya telefon nömrəsi olmalıdır.')


def cursor(row: dict, timestamp: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([str(row[timestamp]), str(row['id'])]).encode()).decode().rstrip('=')


def position(value: str | None) -> tuple | None:
    if not value:
        return None
    if not isinstance(value, str) or len(value) > 400:
        raise TenantLinearError('Səhifə göstəricisi düzgün deyil.')
    try:
        stamp, key = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
        return source_time(stamp), identifier(key)
    except (ValueError, TypeError, UnicodeError):
        raise TenantLinearError('Səhifə göstəricisi düzgün deyil.') from None


def public(row: dict) -> dict:
    return {**{k: str(row.get(k) or '') for k in ('id', 'identifier', 'title', 'summary', 'project_name', 'url')},
        'href': link(row.get('url') or ''), 'status': row['status'],
        'created_at': str(row['created_at']), 'updated_at': str(row['updated_at']),
        'published_at': str(row.get('published_at') or '')}
