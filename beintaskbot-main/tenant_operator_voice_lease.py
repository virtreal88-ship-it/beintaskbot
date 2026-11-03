"""Disabled experimental server-owned distributed voice session lease.

No route imports this module; no production schema or release/recovery exists.
Every function fails closed until separately reviewed gates can be replaced
with a verified control plane. Tests patch the gate on synthetic PostgreSQL only.
"""
import asyncio
from dataclasses import dataclass
import hashlib
import secrets
import threading
import uuid

from tenant_platform import _connect
from tenant_operator_voice_context import authorize, VoiceScope
from tenant_linear_policy import TenantLinearError

# No environment/config/browser override. Never enabled by this candidate.
LEASE_ENFORCEMENT_VERIFIED = False
HEARTBEAT_SECONDS = 10


class LeaseUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class Fence:
    """Server-only capability; never serialize to the browser or logs."""
    session_id: str
    secret: bytes


def _ready():
    if LEASE_ENFORCEMENT_VERIFIED is not True:
        raise LeaseUnavailable('voice_session_lease_unavailable')


def _scope_tuple(expected):
    if not isinstance(expected,VoiceScope):raise LeaseUnavailable('voice_session_scope_invalid')
    return (expected.tenant,expected.actor,expected.channel_id,expected.conversation_id)


def _verify_scope(cur,profile,route,expected,scope):
    if not isinstance(scope,tuple) or scope != _scope_tuple(expected):
        raise LeaseUnavailable('voice_session_scope_invalid')
    # Uses the same real Copilot authorization/route/agent checks as text voice
    # admission. It does not prove atomic revocation at network I/O.
    if authorize(cur,profile,route) != expected:
        raise LeaseUnavailable('voice_session_authority_changed')


def _claim(cur,profile,route,expected,scope,session_id):
    _ready();_verify_scope(cur,profile,route,expected,scope)
    try:
        if not isinstance(session_id,str) or str(uuid.UUID(session_id)) != session_id:raise ValueError()
    except (ValueError,AttributeError):raise LeaseUnavailable('voice_session_invalid') from None
    fence=Fence(session_id,secrets.token_bytes(32))
    cur.execute('''INSERT INTO experiment_voice_session_lease
      (tenant_id,actor_id,channel_id,conversation_id,session_id,fence_hash,heartbeat_until)
      VALUES(%s::uuid,%s,%s::uuid,%s,%s::uuid,%s,clock_timestamp()+(%s*interval '1 second'))
      ON CONFLICT DO NOTHING RETURNING session_id''',(*scope,session_id,hashlib.sha256(fence.secret).digest(),HEARTBEAT_SECONDS))
    if cur.fetchone() is None:raise LeaseUnavailable('voice_session_already_active_or_unverified')
    return fence


def _heartbeat(cur,profile,route,expected,scope,fence):
    _ready()
    if not isinstance(fence,Fence) or not isinstance(fence.secret,bytes) or len(fence.secret)!=32:
        raise LeaseUnavailable('voice_session_invalid')
    if not isinstance(scope,tuple) or scope != _scope_tuple(expected):
        raise LeaseUnavailable('voice_session_scope_invalid')
    try:
        _verify_scope(cur,profile,route,expected,scope)
    except (TenantLinearError,LeaseUnavailable):
        # A previously observed revocation remains terminal even if the member
        # is later re-enabled. Return a sentinel so caller commits quarantine
        # before surfacing the denial; raising here would roll back the fence.
        cur.execute('''UPDATE experiment_voice_session_lease SET state='quarantined'
          WHERE tenant_id=%s::uuid AND actor_id=%s AND channel_id=%s::uuid AND conversation_id=%s
            AND session_id=%s::uuid AND fence_hash=%s AND state='active' ''',
          (*scope,fence.session_id,hashlib.sha256(fence.secret).digest()))
        return 'voice_session_authority_changed'
    cur.execute('''UPDATE experiment_voice_session_lease SET heartbeat_until=clock_timestamp()+(%s*interval '1 second')
      WHERE tenant_id=%s::uuid AND actor_id=%s AND channel_id=%s::uuid AND conversation_id=%s
        AND session_id=%s::uuid AND fence_hash=%s AND state='active' AND heartbeat_until>clock_timestamp()
      RETURNING session_id''',
      (HEARTBEAT_SECONDS,*scope,fence.session_id,hashlib.sha256(fence.secret).digest()))
    if cur.fetchone() is None:
        # Persist observed expiry before exposing the denial. A later backwards
        # wall-clock step cannot revive an already quarantined lease. If this
        # transaction fails, adapter-local terminal state still fails closed;
        # distributed fencing after unknown COMMIT remains a release blocker.
        cur.execute('''UPDATE experiment_voice_session_lease SET state='quarantined'
          WHERE tenant_id=%s::uuid AND actor_id=%s AND channel_id=%s::uuid AND conversation_id=%s
            AND session_id=%s::uuid AND fence_hash=%s AND state='active' AND heartbeat_until<=clock_timestamp()''',
          (*scope,fence.session_id,hashlib.sha256(fence.secret).digest()))
        return 'voice_session_expired_or_unverified'


def _release(*_args,**_kwargs):
    # Provider close, websocket close, wall-clock TTL and a caller boolean do
    # not prove remote compute/billing stopped. No test or caller may open this.
    raise LeaseUnavailable('voice_session_reconciliation_unverified')


class ServerLease:
    """Session interface with trusted server-derived identity, not browser data.

    Async task cancellation cannot cancel a synchronous DB thread. Unknown
    claim/heartbeat outcomes remain fenced; there is no automatic recovery.
    """
    def __init__(self,profile,route,expected,session_id):
        self._profile=dict(profile);self._route=dict(route);self._expected=expected;self._session_id=session_id
        self._terminal=False;self._state_lock=threading.Lock()

    def _mark_terminal(self):
        with self._state_lock:self._terminal=True

    def _ensure_live(self):
        with self._state_lock:
            if self._terminal:raise LeaseUnavailable('voice_session_terminal')

    def _run(self,operation,*args):
        _ready()
        with _connect() as conn:
            with conn.cursor() as cur:
                value=operation(cur,self._profile,self._route,self._expected,*args)
        # The context manager must have committed before success is exposed.
        if operation is _heartbeat and value:
            raise LeaseUnavailable(value)
        return value

    async def _invoke(self,operation,*args):
        self._ensure_live()
        try:
            result=await asyncio.to_thread(self._run,operation,*args)
            # A concurrent failure/cancellation fences this adapter even if
            # another background operation eventually succeeded or committed.
            self._ensure_live()
            return result
        except BaseException:
            self._mark_terminal()
            raise

    async def claim(self,scope):
        return await self._invoke(_claim,scope,self._session_id)

    async def assert_current(self,scope,fence):
        return await self._invoke(_heartbeat,scope,fence)

    async def release(self,scope,fence):
        self._mark_terminal()
        return _release(scope,fence)


def create_server_lease(authority,session_id):
    """Only for a trusted server-created authority; no request can call this.

    A future integration must verify that authority was built from the current
    authenticated server profile and cannot be supplied by browser input.
    This factory deliberately remains gated along with the rest of the module.
    """
    _ready()
    if not isinstance(getattr(authority,'scope',None),VoiceScope):
        raise LeaseUnavailable('voice_session_scope_invalid')
    return ServerLease(authority.profile,authority.route,authority.scope,session_id)
