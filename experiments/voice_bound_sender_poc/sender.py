"""Disabled local-only sender experiment; NO actual socket, key, or live API.

The caller must commit budget + receipt before any hypothetical network write.
Python privacy is not a process/OS security boundary. This synthetic code has
no real factory, authenticated provider config, auth/lease, or safe live path.
"""
import asyncio
import base64
import hashlib
import json

import session_budget as budget
from ledger import local_connect

SYNTHETIC_ENABLED = False
SEND_TIMEOUT = 0.05


def _wire(event):
    return json.dumps(event,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')


class _SyntheticSocket:
    """Only fake sink. It never owns a network socket."""
    __slots__=('__mode','__frames','__closed')
    def __init__(self,mode):
        if mode not in ('normal','timeout','partial_error'):raise ValueError('synthetic_mode_invalid')
        self.__mode=mode;self.__frames=[];self.__closed=False
    async def _write(self,frame):
        if self.__closed:raise RuntimeError('closed')
        if self.__mode=='timeout':
            # Simulate data being accepted before acknowledgement times out.
            self.__frames.append(frame)
            await asyncio.Event().wait()
        if self.__mode=='partial_error':
            self.__frames.append(frame[:max(1,len(frame)//2)])
            raise ConnectionError('synthetic_partial')
        self.__frames.append(frame)
    def _close(self):self.__closed=True
    def _evidence(self):return tuple(self.__frames)


class SyntheticBoundSender:
    """Single owner, no public raw-send API. Testing alone cannot prove isolation."""
    __slots__=('__scope','__dsn','__socket','__lock','__poisoned')
    def __init__(self,dsn,scope,mode='normal'):
        if SYNTHETIC_ENABLED is not True:raise RuntimeError('sender_disabled')
        # Strict validation and independent JSON copy; no mutable caller aliases.
        self.__scope=budget._scope(json.loads(json.dumps(scope,allow_nan=False)))
        self.__dsn=dsn
        self.__socket=_SyntheticSocket(mode)
        self.__lock=asyncio.Lock();self.__poisoned=False
    def evidence(self):return self.__socket._evidence()
    async def _uncertain(self):
        self.__poisoned=True;self.__socket._close()
        try:
            await asyncio.to_thread(self._mark_uncertain)
        except Exception:
            # Reaper is not implemented. Holds remain consumed; no retry.
            pass
    def _mark_uncertain(self):
        with local_connect(self.__dsn) as conn,conn.cursor() as cur:
            budget.mark_uncertain(cur,self.__scope)
    def _commit(self,kind,operation_id,frame,pcm=None,ordinal=None):
        # Validate again inside the transaction worker, rather than relying
        # on its caller to pass a matching PCM frame or a response command.
        if kind=='audio':
            if type(pcm) is not bytes or frame!=_wire({'type':'input_audio_buffer.append','audio':base64.b64encode(pcm).decode('ascii')}):
                raise ValueError('wire_pcm_mismatch')
        elif kind=='response':
            if pcm is not None or frame!=_wire({'type':'response.create'}):raise ValueError('wire_response_mismatch')
        else:raise ValueError('operation_invalid')
        with local_connect(self.__dsn) as conn,conn.cursor() as cur:
            if kind=='audio':issued=budget.issue_audio(cur,self.__scope,operation_id,pcm)
            elif kind=='response':issued=budget.issue_response(cur,self.__scope,operation_id)
            else:raise ValueError('operation_invalid')
            if not issued:return False
            cur.execute('''INSERT INTO saas_voice_bound_sender_receipt
              (operation_id,session_id,operation,scope_fingerprint,wire_sha256,wire_bytes)
              VALUES(%s,%s,%s,%s,%s,%s)''',
              (operation_id,self.__scope['session_id'],kind,budget._fingerprint(self.__scope),hashlib.sha256(frame).hexdigest(),len(frame)))
            # Context manager commits. Any commit exception means NO send,
            # including ambiguous commit; caller marks uncertain and stops.
        return True
    async def _send(self,kind,operation_id,frame,pcm=None,ordinal=None):
        async with self.__lock:
            if self.__poisoned:raise RuntimeError('sender_poisoned')
            try:
                committed=await asyncio.to_thread(self._commit,kind,operation_id,frame,pcm,ordinal)
                if not committed:return False
                await asyncio.wait_for(self.__socket._write(frame),SEND_TIMEOUT)
                return True
            except BaseException:
                # Shield reconciliation; cancellation may still leave it in
                # flight, and caller must not release the budget on ambiguity.
                task=asyncio.create_task(self._uncertain())
                try:await asyncio.shield(task)
                except BaseException:pass
                raise
    async def audio(self,chunk_id,pcm):
        if type(pcm) is not bytes:raise ValueError('immutable_bytes_required')
        frame=_wire({'type':'input_audio_buffer.append','audio':base64.b64encode(pcm).decode('ascii')})
        return await self._send('audio',chunk_id,frame,pcm)
    async def response(self,response_id):
        frame=_wire({'type':'response.create'})
        return await self._send('response',response_id,frame)
