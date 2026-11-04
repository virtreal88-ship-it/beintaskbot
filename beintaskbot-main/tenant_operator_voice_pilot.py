"""Owner-scoped, no-budget voice admission; not connected to live transport.

This policy does not prove the distributed lease or remote shutdown. The
existing live admission gates must remain closed until those are verified.
"""
import asyncio
import uuid


class PilotAdmissionError(Exception):
    pass


class PilotAdmission:
    """In-memory one-use response permits subordinate to an existing lease."""
    def __init__(self, authority, lease, scope, session_id):
        if not isinstance(scope,tuple) or len(scope)!=4 or not all(str(x) for x in scope):
            raise PilotAdmissionError('scope_invalid')
        try:
            if str(uuid.UUID(session_id))!=session_id:raise ValueError()
        except (ValueError,TypeError,AttributeError):
            raise PilotAdmissionError('session_invalid') from None
        self.authority=authority;self.lease=lease;self.scope=scope;self.session_id=session_id
        self._fence=None;self._permits={};self._lock=asyncio.Lock();self._closed=False

    async def admit(self,scope,session_id,fence):
        async with self._lock:
            if self._closed or self._fence is not None or scope!=self.scope or session_id!=self.session_id or not fence:
                raise PilotAdmissionError('admission_invalid')
            await self.authority.check(scope,'pilot_admit')
            await self.lease.assert_current(scope,fence)
            self._fence=fence

    async def issue(self,scope,request_id):
        async with self._lock:
            if self._closed or self._fence is None or scope!=self.scope:
                raise PilotAdmissionError('admission_invalid')
            try:
                if str(uuid.UUID(request_id))!=request_id:raise ValueError()
            except (ValueError,TypeError,AttributeError):
                raise PilotAdmissionError('request_invalid') from None
            if request_id in self._permits:raise PilotAdmissionError('request_reused')
            await self.authority.check(scope,'pilot_issue')
            await self.lease.assert_current(scope,self._fence)
            token=str(uuid.uuid4());self._permits[request_id]=token
            return {'request_id':request_id,'session_id':self.session_id,'token':token}

    async def consume(self,scope,receipt):
        async with self._lock:
            if self._closed or self._fence is None or scope!=self.scope or not isinstance(receipt,dict):return False
            if set(receipt)!={'request_id','session_id','token'} or receipt['session_id']!=self.session_id:return False
            request_id=receipt['request_id']
            token=receipt['token']
            if not isinstance(request_id,str) or not isinstance(token,str) or self._permits.get(request_id)!=token:return False
            # Consume before any awaited check: failures and uncertainty can
            # never turn the same permit into a retry.
            self._permits[request_id]=None
            await self.authority.check(scope,'pilot_dispatch')
            await self.lease.assert_current(scope,self._fence)
            return True

    async def close(self):
        async with self._lock:
            self._closed=True;self._permits.clear()
