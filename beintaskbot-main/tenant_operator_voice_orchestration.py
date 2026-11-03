"""Private Copilot voice composition, closed until safe cutover and billing.

Every object here is server-owned. No provider, credential, model, quote,
tenant or actor may be supplied as a browser-selected implementation. The
public WS path refuses before admission because the legacy text writer can
overwrite voice history and no real provider/dispatch guard is approved.
"""
import asyncio
import json
import uuid
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect
import tenant_operator_voice_context as voice
import tenant_operator_copilot as copilot
import tenant_voice_test_grant as grant
import tenant_operator_voice_source_snapshot as source_snapshot
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
    def __init__(self,authority):self.authority=authority;self._sealed=None

    async def read_authorized(self,session_scope):
        if session_scope!=self.authority.session_scope:raise TenantLinearError('Диалог недоступен.',403)
        snapshot=await asyncio.to_thread(self._seal)
        require_sealed_source_snapshot()
        return snapshot[0]

    async def revalidate(self,session_scope):
        if session_scope!=self.authority.session_scope or self._sealed is None:
            raise TenantLinearError('Версия контекста недоступна.',409)
        expected=self._sealed
        _,current=await asyncio.to_thread(self._capture_stable)
        if self._sealed is not expected or not expected.matches(current):
            raise TenantLinearError('Контекст изменился. Начните разговор заново.',409)
        # This comparison alone cannot fence changes after returning; the live
        # gate is deliberately independent and remains closed.
        return True

    def _seal(self):
        snapshot,sealed=self._capture_stable()
        self._sealed=sealed
        return snapshot,sealed

    def _capture_stable(self):
        first=self._capture_once()
        second=self._capture_once()
        if not first[1].matches(second[1]):
            raise TenantLinearError('Контекст изменился. Начните разговор заново.',409)
        return second

    def _read(self):
        # Compatibility for local, internal diagnostic tests only.
        return self._capture_once()[0]

    def _capture_once(self):
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
        if set(prepared)!={'instructions','lines','extra_context'}:
            raise TenantLinearError('Контекст голосового режима недоступен.',503)
        projection={'existing_copilot_instructions':prepared['instructions'],'lines':prepared['lines'],
                    'extra_context':prepared['extra_context']}
        knowledge=prepared['extra_context'].get('verified_knowledge') or []
        with _connect() as conn:
            with conn.cursor() as cur:
                a.recheck(cur)
                cur.execute('''SELECT external_id FROM saas_crm_messages WHERE tenant_id=%s::uuid
                  AND kommo_lead_id=%s AND channel=%s ORDER BY external_id LIMIT %s''',
                  (s.tenant,s.lead_id,'connect:'+s.channel_id,source_snapshot.MAX_HISTORY_IDS+1))
                all_ids=[row['external_id'] for row in cur.fetchall()]
                selected_ids=[str(row.get('external_id') or '') for row in rows]
                cur.execute('''SELECT id::text AS row_id,external_id,to_jsonb(m) AS record FROM saas_crm_messages m
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND external_id=ANY(%s::text[])
                  ORDER BY external_id''',(s.tenant,s.lead_id,'connect:'+s.channel_id,selected_ids))
                message_records=[dict(row) for row in cur.fetchall()]
                knowledge_state=_knowledge_state(cur,s.tenant,str(agent['agent_id']),knowledge)
        voice.check(a.profile,a.route,expected=s)
        identity={'tenant':s.tenant,'actor':s.actor,'channel_id':s.channel_id,'conversation_id':s.conversation_id,
                  'lead_id':s.lead_id,'agent_binding':s.agent_binding,'config_version':s.config_version}
        sealed=source_snapshot.seal(identity,projection,rows,turns,all_ids,knowledge_state,message_records)
        return projection,sealed


def _knowledge_state(cur,tenant,agent_id,knowledge):
    if not isinstance(knowledge,list) or len(knowledge)>4:
        raise TenantLinearError('Источники знаний недоступны.',409)
    try:
        ids=sorted({str(uuid.UUID(str(item['source_id']))) for item in knowledge})
    except (ValueError,TypeError,KeyError,AttributeError):
        raise TenantLinearError('Источники знаний недоступны.',409) from None
    if not ids:return {'sources':[],'chunks':[],'candidates':[],'instructions':[]}
    cur.execute('''SELECT id::text AS source_id,active,agent_id::text AS agent_id,provenance,updated_at,title,content_hash
      FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[]) ORDER BY id''',(tenant,ids))
    sources=[dict(row) for row in cur.fetchall()]
    by_id={row['source_id']:row for row in sources}
    if set(by_id)!=set(ids) or any(row.get('active') is not True or row.get('agent_id') not in (None,agent_id) for row in sources):
        raise TenantLinearError('Источники знаний изменились.',409)
    cur.execute('''SELECT source_id::text AS source_id,chunk_no,content FROM saas_ai_knowledge_chunks
      WHERE tenant_id=%s::uuid AND source_id=ANY(%s::uuid[]) ORDER BY source_id,chunk_no LIMIT 1001''',(tenant,ids))
    chunks=[dict(row) for row in cur.fetchall()]
    if len(chunks)>1000:raise TenantLinearError('Слишком большая база знаний для проверки.',409)
    for item in knowledge:
        if (by_id[str(item['source_id'])]['title']!=item['title'] or not any(
            row['source_id']==str(item['source_id']) and str(row['content'])[:1200]==item['content'] for row in chunks)):
            raise TenantLinearError('Источники знаний изменились.',409)
    cur.execute('''SELECT to_jsonb(k) AS candidate,to_jsonb(ks) AS link FROM saas_knowledge_candidates k
      JOIN saas_knowledge_candidate_sources ks ON ks.tenant_id=k.tenant_id AND ks.candidate_id=k.id
      WHERE k.tenant_id=%s::uuid AND ks.source_id=ANY(%s::uuid[]) ORDER BY ks.source_id,k.id,ks.agent_id LIMIT 1001''',(tenant,ids))
    candidates=[dict(row) for row in cur.fetchall()]
    if len(candidates)>1000:raise TenantLinearError('Источники знаний недоступны.',409)
    instruction_ids=[]
    for row in sources:
        value=(row.get('provenance') or {}).get('instruction_id')
        if value:
            try:instruction_ids.append(str(uuid.UUID(str(value))))
            except (ValueError,TypeError,AttributeError):raise TenantLinearError('Источники знаний недоступны.',409) from None
    instructions=[]
    if instruction_ids:
        cur.execute("SELECT to_regclass('saas_instruction_lessons') IS NOT NULL AS available")
        if not (cur.fetchone() or {}).get('available'):raise TenantLinearError('Источники знаний недоступны.',409)
        cur.execute('''SELECT to_jsonb(l) AS lesson FROM saas_instruction_lessons l
          WHERE l.tenant_id=%s::uuid AND l.id=ANY(%s::uuid[]) ORDER BY l.id''',(tenant,instruction_ids))
        instructions=[dict(row) for row in cur.fetchall()]
        if len(instructions)!=len(set(instruction_ids)):raise TenantLinearError('Источники знаний недоступны.',409)
    return {'sources':sources,'chunks':chunks,'candidates':candidates,'instructions':instructions}


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


class VoiceHistory:
    def __init__(self,authority):self.authority=authority
    def require_writer_ready(self):require_history_cutover()
    async def append(self,*_args,**_kwargs):
        # Do not write even an apparently complete transcript while an old
        # text worker can overwrite it or after uncertain playback/alignment.
        require_history_cutover()


def _compose_server_session(authority,client,lease,session_id):
    history=VoiceHistory(authority)
    history.require_writer_ready()  # intentionally always raises
    context=CopilotContext(authority)
    ledger=ScopedBudget(authority,session_id)
    provider=create_production_provider(ScopedDispatchGuard(ledger),ServerCredentialLoader(authority))  # independently always raises
    return Session(authority.session_scope,authority=authority,ledger=ledger,lease=lease,
                   context=context,provider=provider,client=client,executor=_EXECUTOR,session_id=session_id)


async def prepare_server_session(profile,route,client,lease):
    # Internal server/test entry; never bind client or lease to request data.
    authority=await ScopedAuthority.create(profile,route)
    return _compose_server_session(authority,client,lease,str(uuid.uuid4()))


def _create_server_lease(_authority,_session_id):
    # The separately developed durable cross-worker lease is not yet reviewed
    # and integrated. Never replace this refusal with a process-local lock.
    raise TenantLinearError('Голосовой режим пока недоступен.',503)


async def open_server_session(request,profile,origin=None):
    # Fail before WS upgrade, microphone streaming or context loading. The
    # serving path must not accept dependency-injection callbacks from HTTP.
    require_history_cutover()
    # Once the immutable cutover gate is deliberately replaced, the remaining
    # factory still has its own history and provider gates. This explicitly
    # binds the HTTP route to the server-owned composition; no browser value
    # selects a provider, implementation, executor or price.
    route={'provider':'connect','lead_id':request.query.get('lead_id'),
           'channel_id':request.query.get('channel_id'),'conversation_id':request.query.get('conversation_id')}
    authority=await ScopedAuthority.create(profile,route)
    session_id=str(uuid.uuid4())
    lease=_create_server_lease(authority,session_id)  # independently hard-off
    from tenant_operator_voice_gateway import make_handler
    async def authenticate(_request):
        await authority.check(authority.session_scope,'connect')
        return authority.session_scope
    def build(scope,client):
        if scope!=authority.session_scope:raise TenantLinearError('Доступ изменился.',403)
        return _compose_server_session(authority,client,lease,session_id)
    return await make_handler(allowed_origins={origin},authenticate=authenticate,build_session=build)(request)
