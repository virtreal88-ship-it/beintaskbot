"""Synthetic authority epoch + lease composition; not production integration.

Only caller-created fixtures install the epoch and triggers. Lock ordering and
writer coverage are incomplete. No recovery or external provider call exists.
"""
import hashlib
import asyncio
import tenant_operator_voice_lease as base


class SyntheticEpochLease(base.ServerLease):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._snapshot_revision=None

    def bind_snapshot(self,scope,revision):
        """Trusted synthetic capture only; not an externally supplied claim."""
        self._ensure_live()
        if scope!=base._scope_tuple(self._expected) or type(revision) is not int or revision<1:
            raise base.LeaseUnavailable('synthetic_snapshot_invalid')
        with self._state_lock:
            if self._snapshot_revision is not None:
                raise base.LeaseUnavailable('synthetic_snapshot_already_bound')
            self._snapshot_revision=revision

    def _run(self,operation,*args):
        base._ready()
        if operation not in (base._claim,base._heartbeat):
            raise base.LeaseUnavailable('synthetic_operation_invalid')
        with base._connect() as conn:
            with conn.cursor() as cur:
                # The synthetic model serializes participating source writers
                # with the lease transaction. Real lock ordering is NOT proven.
                cur.execute('SELECT revision FROM experiment_authority_epoch WHERE tenant_id=%s::uuid FOR UPDATE',
                            (self._expected.tenant,))
                row=cur.fetchone()
                if not row:raise base.LeaseUnavailable('synthetic_epoch_missing')
                revision=row['revision']
                if operation is base._claim:
                    if self._snapshot_revision is None or self._snapshot_revision!=revision:
                        raise base.LeaseUnavailable('synthetic_snapshot_epoch_changed')
                    value=operation(cur,self._profile,self._route,self._expected,*args,self._owner)
                    scope=args[0]
                    cur.execute('''UPDATE experiment_voice_session_lease SET authority_epoch=%s
                      WHERE tenant_id=%s::uuid AND actor_id=%s AND channel_id=%s::uuid AND conversation_id=%s
                        AND session_id=%s::uuid AND owner_hash=%s''',
                      (revision,*scope,value.session_id,hashlib.sha256(self._owner).digest()))
                    if cur.rowcount!=1:raise base.LeaseUnavailable('synthetic_lease_missing')
                else:
                    scope,fence=args
                    if not isinstance(fence,base.Fence) or not isinstance(fence.secret,bytes) or len(fence.secret)!=32:
                        raise base.LeaseUnavailable('voice_session_invalid')
                    if scope!=base._scope_tuple(self._expected):raise base.LeaseUnavailable('voice_session_scope_invalid')
                    cur.execute('''SELECT authority_epoch FROM experiment_voice_session_lease
                      WHERE tenant_id=%s::uuid AND actor_id=%s AND channel_id=%s::uuid AND conversation_id=%s
                        AND session_id=%s::uuid AND fence_hash=%s AND owner_hash=%s AND state='active' FOR UPDATE''',
                      (*scope,fence.session_id,hashlib.sha256(fence.secret).digest(),hashlib.sha256(self._owner).digest()))
                    existing=cur.fetchone()
                    if not existing or existing['authority_epoch'] is None:
                        raise base.LeaseUnavailable('synthetic_lease_missing')
                    if existing['authority_epoch']!=revision:
                        cur.execute('''UPDATE experiment_voice_session_lease SET state='quarantined'
                          WHERE tenant_id=%s::uuid AND actor_id=%s AND channel_id=%s::uuid AND conversation_id=%s
                            AND session_id=%s::uuid AND fence_hash=%s AND owner_hash=%s''',
                          (*scope,fence.session_id,hashlib.sha256(fence.secret).digest(),hashlib.sha256(self._owner).digest()))
                        value='synthetic_authority_epoch_changed'
                    else:
                        value=operation(cur,self._profile,self._route,self._expected,*args,self._owner)
        if operation is base._heartbeat and value:
            raise base.LeaseUnavailable(value)
        return value


class SyntheticSnapshotContext:
    """Read a tiny synthetic projection and its epoch in one PG snapshot.

    This is not the real Copilot prompt or knowledge reader. No raw audio is
    read or stored, and the production context path is deliberately unchanged.
    """
    def __init__(self,profile,route,expected,adapter):
        self.profile=profile;self.route=route;self.expected=expected;self.adapter=adapter

    def _capture(self,scope):
        if scope!=base._scope_tuple(self.expected):raise base.LeaseUnavailable('synthetic_snapshot_scope')
        with base._connect() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            with conn.cursor() as cur:
                cur.execute('SELECT revision FROM experiment_authority_epoch WHERE tenant_id=%s::uuid',(self.expected.tenant,))
                row=cur.fetchone()
                if not row:raise base.LeaseUnavailable('synthetic_epoch_missing')
                base._verify_scope(cur,self.profile,self.route,self.expected,scope)
                cur.execute('SELECT raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s ORDER BY raw::text',
                            (self.expected.tenant,'synthetic'))
                projection={'existing_copilot_instructions':'synthetic','synthetic_rows':[item['raw'] for item in cur.fetchall()]}
        return projection,row['revision']

    async def read_authorized(self,scope):
        projection,revision=await asyncio.to_thread(self._capture,scope)
        self.adapter.bind_snapshot(scope,revision)
        return projection

    async def revalidate(self,scope):
        if scope!=base._scope_tuple(self.expected):raise base.LeaseUnavailable('synthetic_snapshot_scope')
        return True  # claim/heartbeat compare the bound revision, not this stub
