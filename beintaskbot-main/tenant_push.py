"""Tenant-scoped device registration. No push transport or legacy writes."""
import base64
import binascii
import hashlib
import json
import re
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from tenant_platform import _connect, _ensure_schema, _fernet, TenantPlatformError

MAX_DEVICES = 5
PUSH_HOSTS = {'fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com'}


def normalize_subscription(value: object) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get('keys'), dict):
        raise ValueError('Push abunəliyi düzgün deyil.')
    endpoint = value.get('endpoint')
    if not isinstance(endpoint, str) or len(endpoint) > 4096 or re.search(r'[\s\x00-\x1f\x7f\\]', endpoint):
        raise ValueError('Push ünvanı düzgün deyil.')
    try:
        url = urlsplit(endpoint)
        valid = (url.scheme == 'https' and url.hostname in PUSH_HOSTS and url.port in {None, 443}
                 and not url.username and not url.password and not url.fragment and not url.query
                 and '%' not in url.path and bool(url.path.strip('/')))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Push xidməti dəstəklənmir.')
    keys = {}
    for name, size in (('p256dh', 65), ('auth', 16)):
        text = value['keys'].get(name)
        if not isinstance(text, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,100}={0,2}', text):
            raise ValueError('Push açarı düzgün deyil.')
        try:
            raw = base64.b64decode(text.rstrip('=') + '=' * (-len(text.rstrip('=')) % 4), altchars=b'-_', validate=True)
            if len(raw) != size:
                raise ValueError('Invalid size')
            if name == 'p256dh':
                ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        except (ValueError, binascii.Error) as exc:
            raise ValueError('Push açarı düzgün deyil.') from exc
        keys[name] = base64.urlsafe_b64encode(raw).decode().rstrip('=')
    # Canonical authority: host case/default port must not produce another
    # ownership key for the same browser subscription.
    return {'endpoint': 'https://' + url.hostname + url.path, 'keys': keys}


def _identity(profile: dict) -> tuple[str, int]:
    if not profile.get('tenant_id') or profile.get('active') is not True or not profile.get('telegram_id'):
        raise TenantPlatformError('İcazə yoxdur.')
    try:
        user = int(profile['telegram_id'])
    except (ValueError, TypeError) as exc:
        raise TenantPlatformError('İcazə yoxdur.') from exc
    if user <= 0 or isinstance(profile['telegram_id'], bool):
        raise TenantPlatformError('İcazə yoxdur.')
    return str(profile['tenant_id']), user


def register_device(profile: dict, subscription: object, label: str = '') -> dict:
    tenant, user = _identity(profile)
    normalized = normalize_subscription(subscription)
    digest = hashlib.sha256(normalized['endpoint'].encode()).hexdigest()
    if not isinstance(label, str) or len(label) > 80:
        raise ValueError('Cihazın adı çox uzundur.')
    encrypted = _fernet().encrypt(json.dumps(normalized).encode())
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            # Membership row serializes device limits for this company/user.
            cur.execute('''SELECT active FROM saas_tenant_members
                           WHERE tenant_id=%s::uuid AND telegram_id=%s FOR UPDATE''', (tenant, user))
            if not (cur.fetchone() or {}).get('active'):
                raise TenantPlatformError('Əməkdaş aktiv deyil.')
            cur.execute('''SELECT endpoint_hash FROM saas_member_push_devices
                           WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE''', (tenant, user))
            active = {row['endpoint_hash'] for row in cur.fetchall()}
            if digest not in active and len(active) >= MAX_DEVICES:
                raise TenantPlatformError('Ən çox 5 cihaz qoşa bilərsiniz. Köhnə cihazı ayırın.')
            # A shared browser subscription must never be silently taken over
            # by a different Telegram identity. Same person may join companies.
            cur.execute('''INSERT INTO saas_push_devices (endpoint_hash, owner_telegram_id, subscription)
                           VALUES (%s,%s,%s) ON CONFLICT (endpoint_hash) DO UPDATE
                             SET subscription=EXCLUDED.subscription,updated_at=now()
                             WHERE saas_push_devices.owner_telegram_id=EXCLUDED.owner_telegram_id
                           RETURNING endpoint_hash''', (digest, user, encrypted))
            if not cur.fetchone():
                raise TenantPlatformError('Cihaz başqa hesaba bağlıdır. Brauzer abunəliyini yenidən yaradın.')
            cur.execute('''INSERT INTO saas_member_push_devices (tenant_id,telegram_id,endpoint_hash,label)
                           VALUES (%s::uuid,%s,%s,%s) ON CONFLICT (tenant_id,telegram_id,endpoint_hash)
                           DO UPDATE SET active=TRUE,label=EXCLUDED.label,updated_at=now()''',
                        (tenant, user, digest, label.strip()))
        conn.commit()
    return {'device_id': digest, 'label': label.strip(), 'active': True}


def list_devices(profile: dict) -> list[dict]:
    tenant, user = _identity(profile)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT d.endpoint_hash AS device_id,d.label,d.updated_at
                           FROM saas_member_push_devices d
                           JOIN saas_tenant_members m USING (tenant_id,telegram_id)
                           WHERE d.tenant_id=%s::uuid AND d.telegram_id=%s AND d.active=TRUE AND m.active=TRUE
                           ORDER BY d.updated_at DESC LIMIT 5''', (tenant, user))
            rows = cur.fetchall()
    return [{**row, 'updated_at': row['updated_at'].isoformat()} for row in rows]


def disable_device(profile: dict, device_id: object) -> None:
    tenant, user = _identity(profile)
    if not isinstance(device_id, str) or not re.fullmatch(r'[a-f0-9]{64}', device_id):
        raise ValueError('Cihaz kodu düzgün deyil.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_member_push_devices d SET active=FALSE,updated_at=now()
                           WHERE d.tenant_id=%s::uuid AND d.telegram_id=%s AND d.endpoint_hash=%s
                             AND EXISTS (SELECT 1 FROM saas_tenant_members m
                               WHERE m.tenant_id=d.tenant_id AND m.telegram_id=d.telegram_id AND m.active=TRUE)''',
                        (tenant, user, device_id))
        conn.commit()


def disable_browser_device(profile: dict, device_id: object) -> None:
    """Logout ends this person's SaaS bindings on the current browser only."""
    _,user=_identity(profile)
    if not isinstance(device_id,str) or not re.fullmatch(r'[a-f0-9]{64}',device_id):
        raise ValueError('Cihaz kodu düzgün deyil.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_member_push_devices b SET active=FALSE,updated_at=now()
                WHERE b.telegram_id=%s AND b.endpoint_hash=%s AND EXISTS
                  (SELECT 1 FROM saas_push_devices d WHERE d.endpoint_hash=b.endpoint_hash AND d.owner_telegram_id=%s)''',
                        (user,device_id,user))
        conn.commit()
