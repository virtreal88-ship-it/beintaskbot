"""Private Copilot voice composition, closed until safe billing and leasing.

Voice turns use only the provider's temporary per-session conversation. This
module never reads or writes the persistent text Copilot history.
"""
import asyncio
import hashlib
import hmac
import json
import os
import uuid
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect
import tenant_operator_voice_context as voice
import tenant_operator_copilot as copilot
import tenant_voice_test_grant as grant
from tenant_operator_voice_session import Session
from tenant_operator_realtime_wire import create_production_provider, create_pilot_provider
from tenant_operator_voice_pilot import PilotAdmission
from tenant_assigned_agent_prompt import assigned_guidance

_EXECUTOR='copilot-voice-server'


def require_pilot(scope):
    """Admit exactly one server-configured tenant and actor; unset means off."""
    tenant=os.environ.get('COPILOT_VOICE_PILOT_TENANT_ID','')
    actor=os.environ.get('COPILOT_VOICE_PILOT_ACTOR_ID','')
    try:
        if str(uuid.UUID(tenant))!=tenant or not actor.isascii() or not actor.isdecimal() or actor.startswith('0'):
            raise ValueError()
        actor_id=int(actor)
        if not 1<=actor_id<2**63:raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise TenantLinearError('Голосовой режим пока недоступен.',503) from None
    if scope.tenant!=tenant or type(scope.actor) is not int or scope.actor!=actor_id:
        raise TenantLinearError('Голосовой режим недоступен.',403)


def require_voice_budget_ready():
    # Fail closed until the shared durable cap and reconciliation are proven.
    # These are immutable code gates; no browser value or environment flag can
    # open a paid Realtime path.
    if grant.ENFORCEMENT_VERIFIED is not True or grant.RECONCILIATION_VERIFIED is not True:
        raise TenantLinearError('Лимит расходов голосового AI не подтверждён.',503)


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
        require_pilot(scope)
        return cls(profile,route,scope)

    def recheck(self,cur):
        require_pilot(self.scope)
        actual=voice.authorize(cur,self.profile,self.route)
        if actual!=self.scope:raise TenantLinearError('Доступ или настройки изменились.',409)
        return actual

    async def check(self,session_scope,_stage):
        if session_scope!=self.session_scope:raise TenantLinearError('Диалог недоступен.',403)
        require_pilot(self.scope)
        await asyncio.to_thread(voice.check,self.profile,self.route,expected=self.scope)


class CopilotContext:
    def __init__(self,authority):self.authority=authority;self._sealed=None

    async def read_authorized(self,session_scope):
        if session_scope!=self.authority.session_scope:raise TenantLinearError('Диалог недоступен.',403)
        snapshot,fingerprint=await asyncio.to_thread(self._capture)
        self._sealed=fingerprint
        return snapshot

    async def revalidate(self,session_scope):
        if session_scope!=self.authority.session_scope or self._sealed is None:
            raise TenantLinearError('Версия контекста недоступна.',409)
        expected=self._sealed
        _,current=await asyncio.to_thread(self._capture)
        if not hmac.compare_digest(expected,current):
            raise TenantLinearError('Контекст изменился. Начните разговор заново.',409)
        return True

    def _capture(self):
        a=self.authority;s=a.scope
        with _connect() as conn:
            with conn.cursor() as cur:
                a.recheck(cur)
                command={'mode':'chat','prompt':'','lead_id':s.lead_id,'channel_id':s.channel_id,
                         'conversation_id':s.conversation_id,'_agent_binding':s.agent_binding}
                agent=copilot._assigned_context(cur,s.tenant,command)
                if copilot._agent_binding(agent)!=s.agent_binding:
                    raise TenantLinearError('Назначение AI изменилось.',409)
        prompt=copilot.INSTRUCTIONS+assigned_guidance(
            company=agent.get('company_instructions'),agent=agent.get('instructions'),style=agent.get('style'))
        prompt+=('\n\nVoice session: answer naturally by voice in Russian or Azerbaijani, matching the employee. '
                 'Use only this temporary voice session. No CRM tools, customer messaging, or external actions are available. '
                 'Do not claim access to the customer conversation or prior text Copilot history.')
        snapshot={'existing_copilot_instructions':prompt,'lines':[],'extra_context':{}}
        identity={'tenant':s.tenant,'actor':s.actor,'channel_id':s.channel_id,'conversation_id':s.conversation_id,
                  'lead_id':s.lead_id,'agent_binding':s.agent_binding,'config_version':s.config_version}
        canonical=json.dumps({'identity':identity,'snapshot':snapshot},ensure_ascii=False,sort_keys=True,
                             separators=(',',':'),allow_nan=False)
        fingerprint=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        return snapshot,fingerprint


class ScopedBudget:
    def __init__(self,authority,session_id):
        self.authority=authority;self.session_id=session_id

    async def reserve_session(self,scope,session_id):
        if scope!=self.authority.session_scope or session_id!=self.session_id:
            raise TenantLinearError('Область бюджета не совпадает.',403)
        return await asyncio.to_thread(self._reserve_session,scope,session_id)

    def _reserve_session(self,scope,session_id):
        with _connect() as conn:
            with conn.cursor() as cur:
                self.authority.recheck(cur)
                receipt=grant.reserve_session(cur,scope,session_id)
            # The connection context commits before a receipt reaches Session.
        return receipt

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
    async def issue_dispatch(self,item):return await asyncio.to_thread(self._operation,'issue_dispatch',dict(item))
    async def mark_unknown(self,item):return await asyncio.to_thread(self._operation,'mark_unknown',dict(item))
    async def note_actual(self,item,amount):
        # Internal reconciliation only. Never bind this to browser-supplied
        # usage; its authenticity/provider implementation remains blocked.
        return await asyncio.to_thread(self._operation,'note_actual',dict(item),amount)


class ScopedDispatchGuard:
    """Trusted, server-owned CAS; returning True requires committed dispatch.

    A cancelled DB thread may still commit; that hold stays consumed and the
    caller must not retry. This guard does not provide a source/network fence.
    """
    def __init__(self,budget):
        if not isinstance(budget,ScopedBudget):raise ValueError('scoped_budget_required')
        self.budget=budget

    @staticmethod
    def _valid(row,scope,state,hold_id=None):
        if not isinstance(row,dict):return False
        try:
            canonical=grant._scope(scope)
            hold=str(uuid.UUID(str(row['tenant_reservation_id'])))
            return (row['state']==state and row['fingerprint']==grant._fingerprint(canonical)
                    and row['quote_version']==grant.QUOTE and row['model']==grant.MODEL
                    and type(row['reserved_microusd']) is int and row['reserved_microusd']==grant.HOLD
                    and row['actual_microusd'] is None and row['tenant_repair_state']=='intact'
                    and (hold_id is None or hold==hold_id)
                    and all(str(row[key])==str(value) for key,value in canonical.items()))
        except (KeyError,ValueError,TypeError,AttributeError,TenantLinearError):return False

    async def consume(self,scope,receipt):
        self.budget._check_scope(scope)
        if not self._valid(receipt,scope,'claimed'):return False
        hold=str(uuid.UUID(str(receipt['tenant_reservation_id'])))
        # _operation reauthorizes and performs the CAS in one transaction,
        # returning only after its context manager successfully commits.
        dispatched=await self.budget.issue_dispatch(scope)
        return self._valid(dispatched,scope,'dispatching',hold)


class ServerCredentialLoader:
    """Existing company Chat AI credential path, scoped and server-only."""
    def __init__(self,authority):
        if type(authority) is not ScopedAuthority:raise ValueError('scoped_authority_required')
        self.authority=authority

    def _load(self):
        from tenant_platform import _fernet
        with _connect() as conn,conn.cursor() as cur:
            self.authority.recheck(cur)
            cur.execute("SELECT secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",
                        (self.authority.scope.tenant,))
            row=cur.fetchone()
            try:
                secret=json.loads(_fernet().decrypt(bytes(row['secrets'])))
                if (secret.get('tenant_id')!=self.authority.scope.tenant or secret.get('provider')!='chat_ai' or
                    not isinstance(secret.get('api_key'),str) or not secret['api_key']):raise ValueError()
                return secret['api_key']
            except Exception:
                raise TenantLinearError('Проверьте настройки Chat AI компании.',503) from None

    async def load(self):return await asyncio.to_thread(self._load)


def _compose_server_session(authority,client,lease,session_id):
    require_voice_budget_ready()
    context=CopilotContext(authority)
    ledger=ScopedBudget(authority,session_id)
    provider=create_production_provider(ScopedDispatchGuard(ledger),ServerCredentialLoader(authority))  # independently always raises
    return Session(authority.session_scope,authority=authority,ledger=ledger,lease=lease,
                   context=context,provider=provider,client=client,history=None,
                   executor=_EXECUTOR,session_id=session_id)


def _compose_pilot_session(authority,client,lease,session_id):
    policy=PilotAdmission(authority,lease,authority.session_scope,session_id)
    provider=create_pilot_provider(policy,ServerCredentialLoader(authority))
    return Session(authority.session_scope,authority=authority,ledger=None,lease=lease,
                   context=CopilotContext(authority),provider=provider,client=client,history=None,
                   executor=_EXECUTOR,session_id=session_id,pilot_policy=policy)


async def prepare_server_session(profile,route,client,lease):
    # Internal server/test entry; never bind client or lease to request data.
    authority=await ScopedAuthority.create(profile,route)
    return _compose_server_session(authority,client,lease,str(uuid.uuid4()))


def _create_server_lease(_authority,_session_id):
    # PostgreSQL row ownership and a monotonically increasing fencing token
    # supersede process-local locks. Missing schema, stale owners and DB errors
    # all fail closed before provider I/O.
    from tenant_operator_voice_lease import VoiceSessionLease
    try:
        return VoiceSessionLease(_authority,_session_id)
    except (TypeError,ValueError,AttributeError):
        raise TenantLinearError('Голосовой режим пока недоступен.',503) from None


async def open_server_session(request,profile,origin=None):
    # Fail before WS upgrade, microphone streaming or context loading. The
    # serving path must not accept dependency-injection callbacks from HTTP.
    route={'provider':'connect','lead_id':request.query.get('lead_id'),
           'channel_id':request.query.get('channel_id'),'conversation_id':request.query.get('conversation_id')}
    authority=await ScopedAuthority.create(profile,route)
    session_id=str(uuid.uuid4())
    lease=_create_server_lease(authority,session_id)
    from tenant_operator_voice_gateway import make_handler
    async def authenticate(_request):
        await authority.check(authority.session_scope,'connect')
        return authority.session_scope
    def build(scope,client):
        if scope!=authority.session_scope:raise TenantLinearError('Доступ изменился.',403)
        return _compose_pilot_session(authority,client,lease,session_id)
    return await make_handler(allowed_origins={origin},authenticate=authenticate,build_session=build)(request)
