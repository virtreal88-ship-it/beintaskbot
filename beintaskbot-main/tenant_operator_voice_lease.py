"""PostgreSQL-fenced ownership for one active Realtime session per user.

This module deliberately does not create its table at request time. The
review-only additive migration lives with the Realtime candidate. A missing or
incompatible schema therefore keeps session admission closed.
"""
import asyncio
import logging
import uuid

from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect

LEASE_SECONDS = 15
# OpenAI documents a hard maximum Realtime session duration of 60 minutes.
# Only `provider_opened_at`, committed after provider startup succeeds, can
# make an active lease age-recoverable. Uncertain startup remains fail-closed.
# The five-minute margin covers provider shutdown and scheduling delay; a mere
# 15-second lease expiry is intentionally insufficient.
REALTIME_SESSION_MAX_SECONDS = 60 * 60
ACTIVE_RECOVERY_GRACE_SECONDS = 5 * 60
ACTIVE_RECOVERY_AFTER_SECONDS = REALTIME_SESSION_MAX_SECONDS + ACTIVE_RECOVERY_GRACE_SECONDS
_ACTIVE = ('starting', 'active')
_LOG = logging.getLogger(__name__)


def bound_authority_and_lease(authority, lease, scope, session_id):
    """Only the exact production pair can combine authority and lease checks."""
    if type(lease) is not VoiceSessionLease:return False
    from tenant_operator_voice_orchestration import ScopedAuthority
    if type(authority) is not ScopedAuthority:return False
    if (lease.authority is not authority or lease.scope!=scope or
        authority.session_scope!=scope or lease.session_id!=session_id):
        raise TenantLinearError('Область голосового сеанса не совпадает.',403)
    return True


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
                    # Cleanup is an internal server operation. Once the
                    # provider and in-flight work are confirmed drained,
                    # revocation must not strand the exact owned lease.
                    # Claim, renewal and open confirmation still require live
                    # authority; release remains fenced by every identity
                    # field, owner, session, fence and the advisory lock.
                    if operation!='release':self.authority.recheck(cur)
                    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                                ('copilot-voice-lease:' + ':'.join(map(str,self.key)),))
                    if operation == 'claim':
                        cur.execute('''SELECT session_id::text,actor_id,owner_id::text,fence,state,
                          lease_until>now() AS live,
                          (state='active' AND provider_opened_at IS NOT NULL AND
                           provider_opened_at <= now() - (%s * interval '1 second'))
                            AS provider_session_expired
                          FROM saas_operator_voice_lease
                          WHERE tenant_id=%s::uuid AND actor_id=%s FOR UPDATE''',
                          (ACTIVE_RECOVERY_AFTER_SECONDS,*self.key))
                        old = cur.fetchone()
                        # `starting` means provider startup may be in flight or
                        # its outcome is unknown, so never age-reclaim it.
                        # Reclaim `active` only when the provider-open marker
                        # proves startup completed and its maximum lifetime
                        # elapsed. The incremented fence blocks stale owners.
                        if old and not (old['state']=='closed' or
                                        (old['state']=='active' and old['provider_session_expired'] is True)):
                            # Operational diagnosis only: never log tenant,
                            # session, conversation, owner, or provider data.
                            # Expiry is not evidence of provider closure.
                            state=old['state'] if old['state'] in _ACTIVE else 'other'
                            _LOG.info('realtime_voice_lease_claim_refused state=%s lease_live=%s provider_lifetime_elapsed=%s',
                                      state,old['live'] is True,old['provider_session_expired'] is True)
                            conn.commit(); return None
                        if old:
                            next_fence = int(old['fence']) + 1
                            cur.execute('''UPDATE saas_operator_voice_lease SET session_id=%s::uuid,channel_id=%s::uuid,conversation_id=%s,
                              owner_id=%s::uuid,fence=%s,state='starting',provider_opened_at=NULL,lease_until=now()+(%s*interval '1 second'),
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
                        cur.execute('''UPDATE saas_operator_voice_lease SET
                          lease_until=now()+(%s*interval '1 second'),updated_at=now()
                          WHERE tenant_id=%s::uuid AND actor_id=%s AND channel_id=%s::uuid AND conversation_id=%s
                            AND session_id=%s::uuid AND owner_id=%s::uuid AND fence=%s
                            AND state IN ('starting','active') AND lease_until>now() RETURNING fence''',
                          (LEASE_SECONDS,*self.key,self.channel,self.conversation,self.session_id,self.owner_id,fence))
                        row=cur.fetchone()
                        conn.commit()
                        return bool(row and int(row['fence'])==fence)
                    if operation == 'confirm_provider_open':
                        cur.execute('''UPDATE saas_operator_voice_lease SET state='active',
                          provider_opened_at=COALESCE(provider_opened_at,now()),
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

    async def confirm_provider_open(self, scope, fence):
        if scope != self.scope or self.fence is None or fence != self.fence:
            raise TenantLinearError('Владение голосовым сеансом утрачено.',409)
        if not await asyncio.to_thread(self._transaction,'confirm_provider_open',fence):
            raise TenantLinearError('Открытие голосового сеанса не подтверждено.',409)
        return True

    async def release(self, scope, fence):
        if scope != self.scope or self.fence is None or fence != self.fence:
            return False
        released=await asyncio.to_thread(self._transaction,'release',fence)
        if released:self.fence=None
        return released
