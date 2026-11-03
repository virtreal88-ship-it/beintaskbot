"""Synthetic authority epoch + lease composition; not production integration.

Only caller-created fixtures install the epoch and triggers. Lock ordering and
writer coverage are incomplete. No recovery or external provider call exists.
"""
import hashlib
import tenant_operator_voice_lease as base


class SyntheticEpochLease(base.ServerLease):
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
