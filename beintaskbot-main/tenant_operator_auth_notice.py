"""Private owner alert for the configured Operator widget's upstream 401."""
import asyncio
import hashlib
import hmac
import json
import uuid

from tenant_beinsystems_operator import PROVIDER, _connect, _fernet, _link_connection_scope, _operator_row
from tenant_notice_transport import attempt

KEY = 'widget_auth_401_notice'


def _state(row):
    metadata = (row or {}).get('metadata')
    if not isinstance(metadata, dict):
        return {}
    state = metadata.get(KEY)
    return state if isinstance(state, dict) else {}


def _revision(row):
    return hashlib.sha256(bytes(row['secrets'])).hexdigest() if row and row.get('status') == 'connected' and row.get('secrets') else None


def _save(cur, tenant, value):
    if value is None:
        cur.execute("UPDATE saas_tenant_integrations SET metadata=metadata-%s::text WHERE tenant_id=%s AND provider=%s", (KEY, tenant, PROVIDER))
    else:
        cur.execute("""UPDATE saas_tenant_integrations SET metadata=COALESCE(metadata,'{}'::jsonb)
          || jsonb_build_object(%s::text,%s::jsonb) WHERE tenant_id=%s AND provider=%s""",
                    (KEY, json.dumps(value), tenant, PROVIDER))


def _alert_db():
    return _connect(connect_timeout=3, options='-c lock_timeout=500 -c statement_timeout=2000')


def observe(tenant, credentials, *, blocked):
    """Only a 401 or successful list with still-current credentials changes state."""
    with _alert_db() as conn, conn.cursor() as cur:
        row = _operator_row(cur, tenant, lock=True)
        revision = _revision(row)
        if not revision or _link_connection_scope(row) != (
                credentials['generation'], credentials['connection_instance_id']):
            return
        token = json.loads(_fernet().decrypt(bytes(row['secrets'])))['token']
        if not hmac.compare_digest(token.encode(), credentials['token'].encode()):
            return
        state = _state(row)
        if blocked:
            if state.get('credential_revision') == revision:
                return
            _save(cur, tenant, {'id': str(uuid.uuid4()), 'credential_revision': revision, 'status': 'pending'})
        elif state:
            _save(cur, tenant, None)


def _claim(tenant):
    from tenant_connect_health import _owner
    with _alert_db() as conn, conn.cursor() as cur:
        row = _operator_row(cur, tenant, lock=True)
        state = _state(row)
        if state.get('status') != 'pending' or state.get('credential_revision') != _revision(row):
            return None
        owner = _owner(cur, tenant)
        if owner is None:
            return None
        item = {**state, 'status': 'sending', 'recipient_id': owner, 'send': True}
        _save(cur, tenant, {**state, 'status': 'sending'})
    return item


def _still_current(tenant, item):
    from tenant_connect_health import _owner
    with _alert_db() as conn, conn.cursor() as cur:
        row = _operator_row(cur, tenant)
        state = _state(row)
        return (state.get('status') == 'sending' and state.get('id') == item['id']
                and _revision(row) == item['credential_revision']
                and _owner(cur, tenant) == item['recipient_id'])


def _notice(_):
    return ('BeinSystems Operator отклонил авторизацию CRM (401). '
            'Новые сообщения из виджета могут не поступать. '
            'Откройте «Интеграции → BeinSystems Operator» и обновите подключение.\n'
            'https://crm.pro.az/app')


async def deliver(bot, logger):
    from tenant_operator_widget_sync import enabled
    tenant = enabled()
    if not tenant or bot is None:
        return
    item = await asyncio.to_thread(_claim, tenant)
    if item is None or not await asyncio.to_thread(_still_current, tenant, item):
        return
    result = await attempt(item, 'telegram', bot, text=_notice, parts=lambda text: [text])
    if result.status != 'delivered':
        logger.warning('Operator 401 owner Telegram notice outcome unknown; no automatic replay')
