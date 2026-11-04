"""Private Copilot voice admission: same tenant/route/agent boundaries.

Voice orchestration builds instructions without reading persistent text
history. This module never opens a provider; session admission stays closed
until budget and lease protections are verified.
"""
from dataclasses import dataclass
from tenant_linear_policy import TenantLinearError, identifier
from tenant_operator_access import access, assert_route
from tenant_operator_copilot import _assigned_context, _agent_binding, _MODEL_ID
from tenant_connect_identity import conflicted
from tenant_ai_group_coordination import group_jid
from tenant_connect_direct_identity import direct_identity
from tenant_platform import _connect


@dataclass(frozen=True, repr=False)
class VoiceScope:
    tenant: str
    actor: int
    channel_id: str
    conversation_id: str
    lead_id: int
    agent_binding: str
    config_version: str


def _route(data):
    if not isinstance(data,dict) or set(data)!={'provider','lead_id','channel_id','conversation_id'} or data['provider']!='connect':
        raise TenantLinearError('Некорректный диалог.',400)
    channel=identifier(data['channel_id'])
    conversation=data['conversation_id']
    if (not isinstance(conversation,str) or not conversation or len(conversation)>160 or conversation!=conversation.strip()
        or any(ord(char)<32 or ord(char)==127 for char in conversation)):
        raise TenantLinearError('Некорректный диалог.',400)
    value=data['lead_id']
    if type(value) is int:
        lead=value
    elif isinstance(value,str) and value.isascii() and value.isdecimal() and 1<=len(value)<=19 and value[0]!='0':
        lead=int(value)
    else:
        raise TenantLinearError('Некорректный диалог.',400)
    if not 1<=lead<2**63:
        raise TenantLinearError('Некорректный диалог.',400)
    return {'channel_id':channel,'conversation_id':conversation,'lead_id':lead}


def authorize(cur, profile, data):
    route_data=_route(data)
    # Group coordination has its own authority and lifecycle. Until voice
    # has an independently reviewed group source/permission fence, do not
    # admit even an otherwise valid group to voice preflight.
    if group_jid(route_data['conversation_id']) or not direct_identity(route_data['conversation_id']):
        raise TenantLinearError('Голосовой режим пока доступен только для личных диалогов с определённым типом.',409)
    tenant,actor,policy=access(cur,profile,'dialogs')
    route=assert_route(cur,tenant,policy,'connect',route_data['conversation_id'],route_data['channel_id'],route_data['lead_id'])
    if str(route.get('conversation_kind') or '').strip().casefold() not in {'contact','direct','personal'}:
        raise TenantLinearError('Голосовой режим пока доступен только для личных диалогов с определённым типом.',409)
    if int(route.get('lead_id') or 0)!=route_data['lead_id']:
        raise TenantLinearError('Диалог изменился. Обновите его.',409)
    if not policy.can_use_chat_ai():raise TenantLinearError('У сотрудника нет доступа к Chat AI.',403)
    if conflicted(cur,tenant,route_data['channel_id'],route_data['conversation_id']):
        raise TenantLinearError('История диалога требует проверки.',409)
    cur.execute("SELECT metadata,updated_at,secrets IS NOT NULL AS configured FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai' FOR SHARE",(tenant,))
    connection=cur.fetchone() or {}
    config=connection.get('metadata') if isinstance(connection.get('metadata'),dict) else {}
    if (connection.get('configured') is not True or config.get('enabled') is not True or config.get('consent') is not True
        or config.get('audio_enabled') is not True or config.get('copilot_enabled',True) is not True or not connection.get('updated_at')):
        raise TenantLinearError('Голосовой AI для компании недоступен.',409)
    cur.execute('SELECT emergency_stop FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid FOR SHARE',(tenant,))
    if (cur.fetchone() or {}).get('emergency_stop') is True:raise TenantLinearError('AI компании остановлен.',409)
    agent=_assigned_context(cur,tenant,route_data)
    if not _MODEL_ID.fullmatch(str(agent.get('model') or '')):
        raise TenantLinearError('Назначьте корректную модель агенту.',409)
    return VoiceScope(tenant,actor,route_data['channel_id'],route_data['conversation_id'],route_data['lead_id'],
                      _agent_binding(agent),str(connection['updated_at']))


def check(profile,data,*,expected=None):
    with _connect() as conn:
        with conn.cursor() as cur:
            scope=authorize(cur,profile,data)
            if expected is not None and scope!=expected:
                raise TenantLinearError('Доступ или настройки изменились.',409)
            return scope


def context(*_args,**_kwargs):
    # The production provider admission and shared spend cap are not verified.
    raise TenantLinearError('Голосовой режим пока недоступен.',503)
