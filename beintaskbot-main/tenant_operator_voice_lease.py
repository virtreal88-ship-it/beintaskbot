"""PostgreSQL-fenced ownership for one active Realtime session per user.

This module deliberately does not create its table at request time. The
review-only additive migration lives with the Realtime candidate. A missing or
incompatible schema therefore keeps session admission closed.
"""
import asyncio
import uuid

from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect

LEASE_SECONDS = 15
_ACTIVE = ('starting', 'active')


class VoiceSessionLease:
    def __init__(self, authority, session_id, *, owner_id=None):
        if not isinstance(session_id, str) or str(uuid.UUID(session_id)) != session_id:
            raise ValueError('invalid_session_id')
        self.authority = authority
        self.session_id = session_id
        self.owner_id = owner_id or str(uuid.uuid4())
        if str(uuid.UUID(self.owner_id)) != self.owner_id:
            raise ValueError('invalid_owner_id')
        self.scope = authority.session_scope
        tenant, actor, channel, conversation = self.scope
        self.key = (str(tenant), actor)
        self.channel = str(channel)
        self.conversation = str(conversation)
        self.actor = actor
        self.fence = None

    def _transaction(self, operation, fence=None):
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    self.authority.recheck(cur)
                    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                                ('copilot-voice-lease:' + ':'.join(map(str,self.key)),))
                    if operation == 'claim':
                        cur.execute('''SELECT session_id::text,actor_id,owner_id::text,fence,state,
                          lease_until>now() AS live FROM saas_operator_voice_lease
                          WHERE tenant_id=%s::uuid AND actor_id=%s FOR UPDATE''', self.key)
                        old = cur.fetchone()
                        if old and old['state'] in _ACTIVE and old['live'] is True:
                            conn.commit(); return None
                        if old:
                            next_fence = int(old['fence']) + 1
                            cur.execute('''UPDATE saas_operator_voice_lease SET session_id=%s::uuid,channel_id=%s::uuid,conversation_id=%s,
                              owner_id=%s::uuid,fence=%s,state='starting',lease_until=now()+(%s*interval '1 second'),
                              updated_at=now(),closed_at=NULL WHERE tenant_id=%s::uuid
                              AND actor_id=%s AND fence=%s RETURNING fence''',
                              (self.session_id,self.channel,self.conversation,self.owner_id,next_fence,LEASE_SECONDS,*self.key,old['fence']))
                        else:
                            next_fence = 1
                            cur.execute('''INSERT INTO saas_operator_voice_lease
                              (tenant_id,actor_id,channel_id,conversation_id,session_id,owner_id,fence,state,lease_until)
                              VALUES(%s::uuid,%s,%s::uuid,%s,%s::uuid,%s::uuid,%s,'starting',
                                now()+(%s*interval '1 second')) RETURNING fence''',
                              (*self.key,self.channel,self.conversation,self.session_id,self.owner_id,next_fence,LEASE_SECONDS))
                        row=cur.fetchone()
                        if not row or int(row['fence'])!=next_fence:
                            raise TenantLinearError('Голосовой сеанс уже открыт.',409)
                        conn.commit()
                        return next_fence
                    if operation == 'renew':
                        cur.execute('''UPDATE saas_operator_voice_lease SET state='active',
                          lease_until=now()+(%s*interval '1 second'),updated_at=now()
                          WHERE tenant_id=%s::uuid AND actor_id=%s AND channel_id=%s::uuid AND conversation_id=%s
                            AND session_id=%s::uuid AND owner_id=%s::uuid AND fence=%s
                            AND state IN ('starting','active') AND lease_until>now() RETURNING fence''',
                          (LEASE_SECONDS,*self.key,self.channel,self.conversation,self.session_id,self.owner_id,fence))
                        row=cur.fetchone()
                        conn.commit()
                        return bool(row and int(row['fence'])==fence)
                    if operation == 'release':
                        cur.execute('''UPDATE saas_operator_voice_lease SET state='closed',lease_until=now(),
                          updated_at=now(),closed_at=now() WHERE tenant_id=%s::uuid AND actor_id=%s AND channel_id=%s::uuid
                            AND conversation_id=%s AND session_id=%s::uuid AND owner_id=%s::uuid
                          AND fence=%s AND state IN ('starting','active') RETURNING fence''',
                          (*self.key,self.channel,self.conversation,self.session_id,self.owner_id,fence))
                        row=cur.fetchone()
                        conn.commit()
                        return bool(row and int(row['fence'])==fence)
                    raise ValueError('unknown_lease_operation')
        except TenantLinearError:
            raise
        except Exception:
            raise TenantLinearError('Состояние голосового сеанса не подтверждено.',503) from None

    async def claim(self, scope):
        if scope != self.scope or self.fence is not None:
            raise TenantLinearError('Область голосового сеанса не совпадает.',403)
        fence=await asyncio.to_thread(self._transaction,'claim')
        if fence is not None:self.fence=fence
        return fence

    async def assert_current(self, scope, fence):
        if scope != self.scope or self.fence is None or fence != self.fence:
            raise TenantLinearError('Владение голосовым сеансом утрачено.',409)
        if not await asyncio.to_thread(self._transaction,'renew',fence):
            raise TenantLinearError('Владение голосовым сеансом утрачено.',409)
        return True

    async def release(self, scope, fence):
        if scope != self.scope or self.fence is None or fence != self.fence:
            return False
        released=await asyncio.to_thread(self._transaction,'release',fence)
        if released:self.fence=None
        return released
