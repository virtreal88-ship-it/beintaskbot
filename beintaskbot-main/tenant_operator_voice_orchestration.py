"""Private Copilot voice composition, closed until safe cutover and billing.

Every object here is server-owned. No provider, credential, model, quote,
tenant or actor may be supplied as a browser-selected implementation. The
public WS path refuses before admission because the legacy text writer can
overwrite voice history and no real provider/dispatch guard is approved.
"""
import asyncio
import hashlib
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
    # Snapshot comparison detects tested changes, but cannot fence ABA changes
    # or concurrent changes between the final check and external provider I/O.
    # Do not enable voice until that boundary has a reviewed safe protocol.
    raise TenantLinearError('Контекст голосового режима пока недоступен.',503)


def require_voice_history_provenance():
    # A UUID and shaped transcript are not proof of an active server-owned
    # session, current lease, event ordering or actual playback. Independent
    # immutable refusal until those bindings are implemented and reviewed.
    raise TenantLinearError('Происхождение голосовой истории не подтверждено.',503)


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
    def __init__(self,authority,session_id=None,lease=None):
        self.authority=authority;self.session_id=session_id;self.lease=lease
        self.lease_fence=None;self.clear_revision=None
    def require_writer_ready(self):require_history_cutover()
    def _begin(self,lease_fence):
        require_history_cutover();require_voice_history_provenance()
        s=self.authority.scope
        if type(lease_fence) is not int or lease_fence<1 or self.lease is None or self.lease.fence!=lease_fence:
            raise TenantLinearError('Владение голосовым сеансом не подтверждено.',409)
        with _connect() as conn,conn.cursor() as cur:
            self.authority.recheck(cur)
            key=(s.tenant,s.actor,s.channel_id,s.conversation_id)
            cur.execute('''INSERT INTO saas_operator_copilot_threads
              (tenant_id,requested_by,channel_id,conversation_id,turns,clear_revision)
              VALUES(%s::uuid,%s,%s,%s,'[]'::jsonb,0)
              ON CONFLICT(tenant_id,requested_by,channel_id,conversation_id) DO NOTHING''',key)
            cur.execute('''SELECT clear_revision,turns,updated_at < now()-interval '60 days' AS expired
              FROM saas_operator_copilot_threads WHERE tenant_id=%s::uuid AND requested_by=%s
              AND channel_id=%s AND conversation_id=%s FOR UPDATE''',key)
            row=cur.fetchone()
            if not row or type(row.get('clear_revision')) is not int or not isinstance(row.get('turns'),list):
                raise TenantLinearError('История AI недоступна.',503)
            if row.get('expired') is True and row['turns']:
                cur.execute('''UPDATE saas_operator_copilot_threads SET turns='[]'::jsonb,
                  clear_revision=clear_revision+1,history_revision=history_revision+1,updated_at=now()
                  WHERE tenant_id=%s::uuid AND requested_by=%s AND channel_id=%s AND conversation_id=%s''',key)
                conn.commit()
                raise TenantLinearError('История AI истекла. Обновите разговор.',409)
            revision=row['clear_revision']
            cur.execute('''UPDATE saas_operator_copilot_threads SET voice_history_seen=true,
              history_revision=history_revision+1,updated_at=now()
              WHERE tenant_id=%s::uuid AND requested_by=%s AND channel_id=%s AND conversation_id=%s''',key)
            cur.execute('''INSERT INTO saas_operator_voice_sessions
              (session_id,tenant_id,actor_id,channel_id,conversation_id,agent_binding,clear_revision,
               next_sequence,lease_fence,state,started_at)
              VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,0,%s,'active',now())
              ON CONFLICT(session_id) DO NOTHING''',
              (self.session_id,s.tenant,s.actor,s.channel_id,s.conversation_id,s.agent_binding,revision,lease_fence))
            cur.execute('''SELECT tenant_id::text,actor_id,channel_id::text,conversation_id,agent_binding,
              clear_revision,lease_fence,state FROM saas_operator_voice_sessions
              WHERE session_id=%s::uuid FOR UPDATE''',(self.session_id,))
            session=cur.fetchone()
            if (not session or session['tenant_id']!=s.tenant or session['actor_id']!=s.actor or
                session['channel_id']!=s.channel_id or session['conversation_id']!=s.conversation_id or
                session['agent_binding']!=s.agent_binding or session['clear_revision']!=revision or
                session['lease_fence']!=lease_fence or session['state']!='active'):
                raise TenantLinearError('Область голосовой истории не совпадает.',409)
        self.clear_revision=revision;self.lease_fence=lease_fence
        return revision
    async def begin(self,lease_fence):
        return await asyncio.to_thread(self._begin,lease_fence)
    def _close(self,lease_fence):
        if self.clear_revision is None or lease_fence!=self.lease_fence:return False
        s=self.authority.scope
        with _connect() as conn,conn.cursor() as cur:
            self.authority.recheck(cur)
            cur.execute('''UPDATE saas_operator_voice_sessions SET state='closed',ended_at=now()
              WHERE session_id=%s::uuid AND tenant_id=%s::uuid AND actor_id=%s AND channel_id=%s::uuid
                AND conversation_id=%s AND agent_binding=%s AND clear_revision=%s AND lease_fence=%s
                AND state='active' RETURNING session_id''',
              (self.session_id,s.tenant,s.actor,s.channel_id,s.conversation_id,s.agent_binding,
               self.clear_revision,lease_fence))
            return bool(cur.fetchone())
    async def close(self,lease_fence):
        result=await asyncio.to_thread(self._close,lease_fence)
        if result:self.clear_revision=None
        return result
    def _append(self,entry,clear_revision):
        require_history_cutover()
        require_voice_history_provenance()
        from tenant_operator_copilot_threads import _validated_entries,append,ThreadCleared
        if (type(clear_revision) is not int or clear_revision<0 or not isinstance(entry,dict) or
            not isinstance(self.session_id,str) or str(uuid.UUID(self.session_id))!=self.session_id or
            entry.get('voice_session_id')!=self.session_id):
            raise ValueError('voice_history_scope_invalid')
        s=self.authority.scope
        _validated_entries(s.agent_binding,[entry])
        if (entry.get('transcript_quality')=='human_verified' or
            entry.get('transcript_status')=='complete' and entry.get('transcript_quality')!='automatic_unverified'):
            raise TenantLinearError('Происхождение голосовой реплики не подтверждено.',503)
        if not {'voice_session_id','turn_sequence','transcript_status','transcript_quality'}.issubset(entry):
            raise ValueError('voice_history_metadata_required')
        canonical=json.dumps({'entry':entry,'clear_revision':clear_revision},ensure_ascii=False,
                             sort_keys=True,separators=(',',':'),allow_nan=False)
        digest=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        cleared=False
        with _connect() as conn,conn.cursor() as cur:
            self.authority.recheck(cur)
            cur.execute('SELECT * FROM saas_operator_voice_sessions WHERE session_id=%s::uuid FOR UPDATE',(self.session_id,))
            row=cur.fetchone() or {}
            if (str(row.get('tenant_id'))!=s.tenant or row.get('actor_id')!=s.actor or row.get('channel_id')!=s.channel_id or
                row.get('conversation_id')!=s.conversation_id or row.get('agent_binding')!=s.agent_binding or
                row.get('clear_revision')!=clear_revision or row.get('lease_fence')!=self.lease_fence or
                row.get('state')!='active'):raise TenantLinearError('Голосовая история изменилась.',409)
            # A clear must be checked even for an idempotent retry.
            cur.execute('''SELECT clear_revision FROM saas_operator_copilot_threads WHERE tenant_id=%s::uuid
              AND requested_by=%s AND channel_id=%s AND conversation_id=%s FOR UPDATE''',
              (s.tenant,s.actor,s.channel_id,s.conversation_id))
            thread=cur.fetchone();revision=thread['clear_revision'] if thread else 0
            if revision!=clear_revision:raise TenantLinearError('Голосовая история очищена.',409)
            sequence=entry['turn_sequence']
            cur.execute('SELECT digest FROM saas_operator_voice_turns WHERE session_id=%s::uuid AND turn_sequence=%s',
                        (self.session_id,sequence))
            previous=cur.fetchone()
            if previous:
                if previous['digest']!=digest:raise TenantLinearError('Голосовая реплика изменилась.',409)
                return 'duplicate'
            if row.get('next_sequence')!=sequence:raise TenantLinearError('Нарушен порядок голосовых реплик.',409)
            try:
                append(cur,s.tenant,s.actor,s.channel_id,s.conversation_id,clear_revision,s.agent_binding,[entry])
            except ThreadCleared:
                # Commit any atomic expiry scrub before returning failure.
                # No turn receipt or sequence increment occurs on this path.
                cleared=True
            if not cleared:
                cur.execute('INSERT INTO saas_operator_voice_turns(session_id,turn_sequence,digest) VALUES(%s::uuid,%s,%s)',
                            (self.session_id,sequence,digest))
                cur.execute('UPDATE saas_operator_voice_sessions SET next_sequence=next_sequence+1 WHERE session_id=%s::uuid',
                            (self.session_id,))
        if cleared:raise TenantLinearError('Голосовая история очищена или истекла.',409)
        return 'appended'

    async def append(self,entry,clear_revision=None):
        # Candidate schema/migration and every old text writer must be cut
        # over before writing, including when a client retries a prior turn.
        require_history_cutover()
        revision=self.clear_revision if clear_revision is None else clear_revision
        if self.lease is None or self.lease_fence is None:
            raise TenantLinearError('Владение голосовым сеансом не подтверждено.',409)
        await self.lease.assert_current(self.authority.session_scope,self.lease_fence)
        return await asyncio.to_thread(self._append,dict(entry),revision)

    async def flush(self,entries,lease_fence):
        if lease_fence!=self.lease_fence or not isinstance(entries,list) or len(entries)>20:
            return False
        for entry in entries:
            await self.append(entry,self.clear_revision)
        return await self.close(lease_fence)


def _compose_server_session(authority,client,lease,session_id):
    history=VoiceHistory(authority,session_id,lease)
    history.require_writer_ready()  # intentionally always raises
    context=CopilotContext(authority)
    ledger=ScopedBudget(authority,session_id)
    provider=create_production_provider(ScopedDispatchGuard(ledger),ServerCredentialLoader(authority))  # independently always raises
    return Session(authority.session_scope,authority=authority,ledger=ledger,lease=lease,
                   context=context,provider=provider,client=client,history=history,
                   executor=_EXECUTOR,session_id=session_id)


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
