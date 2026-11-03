"""Private Copilot voice composition, closed until safe cutover and billing.

Every object here is server-owned. No provider, credential, model, quote,
tenant or actor may be supplied as a browser-selected implementation. The
public WS path refuses before admission because the legacy text writer can
overwrite voice history and no real provider/dispatch guard is approved.
"""
import asyncio
import uuid
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect
import tenant_operator_voice_context as voice
import tenant_operator_copilot as copilot
import tenant_voice_test_grant as grant
from tenant_operator_voice_session import Session
from tenant_operator_realtime_wire import create_production_provider

_EXECUTOR='copilot-voice-server'


def require_history_cutover():
    # Immutable code gate: an environment flag or HTTP parameter cannot
    # override incompatible old writers or missing session/turn idempotency.
    raise TenantLinearError('История голосового режима пока недоступна.',503)


def require_sealed_source_snapshot():
    # No authorised immutable source/version snapshot is yet connected to the
    # existing per-response freshness check. Never pass a prompt to a provider
    # until server-side source and latest-message revalidation is implemented.
    raise TenantLinearError('Контекст голосового режима пока недоступен.',503)


class ScopedAuthority:
    def __init__(self,profile,route,scope):
        self.profile={'tenant_id':scope.tenant,'telegram_id':scope.actor}
        self.route=dict(route)
        self.scope=scope
        self.session_scope=(scope.tenant,scope.actor,scope.channel_id,scope.conversation_id)

    @classmethod
    async def create(cls,profile,route):
        # Sync DB work gets a dedicated connection in a worker thread.
        if not isinstance(profile,dict) or not isinstance(route,dict):
            raise TenantLinearError('Нет доступа.',403)
        profile=dict(profile);route=dict(route)
        scope=await asyncio.to_thread(voice.check,profile,route)
        return cls(profile,route,scope)

    def recheck(self,cur):
        actual=voice.authorize(cur,self.profile,self.route)
        if actual!=self.scope:raise TenantLinearError('Доступ или настройки изменились.',409)
        return actual

    async def check(self,session_scope,_stage):
        if session_scope!=self.session_scope:raise TenantLinearError('Диалог недоступен.',403)
        await asyncio.to_thread(voice.check,self.profile,self.route,expected=self.scope)


class CopilotContext:
    def __init__(self,authority):self.authority=authority

    async def read_authorized(self,session_scope):
        if session_scope!=self.authority.session_scope:raise TenantLinearError('Диалог недоступен.',403)
        snapshot=await asyncio.to_thread(self._read)
        require_sealed_source_snapshot()
        return snapshot

    def _read(self):
        a=self.authority;s=a.scope
        with _connect() as conn:
            with conn.cursor() as cur:
                a.recheck(cur)
                command={'mode':'chat','prompt':'','lead_id':s.lead_id,'channel_id':s.channel_id,
                         'conversation_id':s.conversation_id,'_agent_binding':s.agent_binding}
                agent=copilot._assigned_context(cur,s.tenant,command)
                command['_agent_id']=str(agent['agent_id'])
                cur.execute("SELECT metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai' FOR SHARE",(s.tenant,))
                connection=cur.fetchone() or {};config=connection.get('metadata') or {}
                if str(connection.get('updated_at') or '')!=s.config_version:
                    raise TenantLinearError('Настройки AI изменились.',409)
                rows,turns=copilot._read_history(cur,s.tenant,s.actor,command)
        # The database transaction and its FOR UPDATE lock have ended before
        # building context. A real dispatch must recheck authority and source.
        prepared=copilot._draft_context(s.tenant,s.actor,command,config,connection,
            {'api_key':'CONTEXT_ONLY_NO_CREDENTIAL'},str(agent['model']),agent,rows,turns,context_only=True)
        voice.check(a.profile,a.route,expected=s)
        if set(prepared)!={'instructions','lines','extra_context'}:
            raise TenantLinearError('Контекст голосового режима недоступен.',503)
        return {'existing_copilot_instructions':prepared['instructions'],'lines':prepared['lines'],
                'extra_context':prepared['extra_context']}


class ScopedBudget:
    def __init__(self,authority,session_id):
        self.authority=authority;self.session_id=session_id

    def _check_scope(self,item):
        s=self.authority.scope
        if (not isinstance(item,dict) or item.get('tenant_id')!=s.tenant or item.get('actor_id')!=s.actor or
            item.get('channel_id')!=s.channel_id or item.get('conversation_id')!=s.conversation_id or
            item.get('session_id')!=self.session_id or item.get('executor')!=_EXECUTOR):
            raise TenantLinearError('Область бюджета не совпадает.',403)

    def _operation(self,name,item,amount=None):
        self._check_scope(item)
        with _connect() as conn:
            with conn.cursor() as cur:
                # Reserve/claim are admissions and require live authority.
                # Reconciliation only preserves a pre-existing scoped hold;
                # revocation must not prevent it becoming unknown.
                if name not in {'mark_unknown','note_actual'}:self.authority.recheck(cur)
                result=getattr(grant,name)(cur,item,amount) if name=='note_actual' else getattr(grant,name)(cur,item)
            # Leaving the context commits before a successful receipt is
            # returned to the session; uncertain outcomes retain their hold.
        return result

    async def reserve(self,item):return await asyncio.to_thread(self._operation,'reserve',dict(item))
    async def claim(self,item):return await asyncio.to_thread(self._operation,'claim',dict(item))
    async def mark_unknown(self,item):return await asyncio.to_thread(self._operation,'mark_unknown',dict(item))
    async def note_actual(self,item,amount):
        # Internal reconciliation only. Never bind this to browser-supplied
        # usage; its authenticity/provider implementation remains blocked.
        return await asyncio.to_thread(self._operation,'note_actual',dict(item),amount)


class VoiceHistory:
    def __init__(self,authority):self.authority=authority
    def require_writer_ready(self):require_history_cutover()
    async def append(self,*_args,**_kwargs):
        # Do not write even an apparently complete transcript while an old
        # text worker can overwrite it or after uncertain playback/alignment.
        require_history_cutover()


async def prepare_server_session(profile,route,client,lease):
    # All inputs are created by the authenticated server boundary; the
    # browser cannot choose model, executor, provider, auth, ledger or lease.
    authority=await ScopedAuthority.create(profile,route)
    history=VoiceHistory(authority)
    history.require_writer_ready()  # intentionally always raises
    context=CopilotContext(authority)
    provider=create_production_provider()  # independently always raises
    # Unreachable until both immutable gates are reviewed and replaced.
    session_id=str(uuid.uuid4())
    ledger=ScopedBudget(authority,session_id)
    session=Session(authority.session_scope,authority=authority,ledger=ledger,lease=lease,
                    context=context,provider=provider,client=client,executor=_EXECUTOR,session_id=session_id)
    return session


async def open_server_session(request,profile):
    # Fail before WS upgrade, microphone streaming or context loading. The
    # serving path must not accept dependency-injection callbacks from HTTP.
    require_history_cutover()
    # Once the immutable cutover gate is deliberately replaced, the remaining
    # factory still has its own history and provider gates. This explicitly
    # binds the HTTP route to the server-owned composition; no browser value
    # selects a provider, implementation, executor or price.
    route={'provider':'connect','lead_id':request.query.get('lead_id'),
           'channel_id':request.query.get('channel_id'),'conversation_id':request.query.get('conversation_id')}
    return await prepare_server_session(profile,route,None,None)
