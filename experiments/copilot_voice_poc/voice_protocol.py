"""Offline-only protocol experiment. No credentials, sockets, audio files or app routes.

The real integration must obtain its context and authorization from the existing
Copilot server. This module deliberately has no way to open a provider session.
"""
from __future__ import annotations

import base64
import binascii
import time
from dataclasses import dataclass
from typing import Callable


class Closed(RuntimeError):
    pass


@dataclass(frozen=True)
class Limits:
    seconds: int = 300
    idle_seconds: int = 60
    input_bytes: int = 14_400_000  # Five minutes of mono PCM16 at 24 kHz.
    output_bytes: int = 14_400_000
    chunk_bytes: int = 48_000
    events: int = 4000
    transcript_chars: int = 6000


class OfflineVoiceSession:
    """A fail-closed server-owned event boundary for a synthetic provider.

    Send callbacks must be local test doubles. Never attach this experiment to
    an actual endpoint: authority, accounting and history commits are not wired.
    """
    def __init__(self, scope: tuple, allowed: Callable[[], bool], upstream: Callable,
                 downstream: Callable, *, clock=time.monotonic, limits=Limits()):
        if len(scope) != 5 or any(not str(part) for part in scope):
            raise ValueError('invalid scoped identity')
        self.scope, self.allowed, self.upstream, self.downstream = scope, allowed, upstream, downstream
        self.clock, self.limits = clock, limits
        self.started = self.activity = clock()
        self.input_size = self.output_size = self.event_count = 0
        self.item, self.sent_ms, self.played_ms = None, 0, 0
        self.interrupted = set()
        self.closed = False

    def stop(self, reason='ended'):
        if not self.closed:
            self.closed = True
            self.downstream({'type': 'closed', 'reason': reason})

    def _guard(self):
        if self.closed:
            raise Closed('closed')
        now = self.clock()
        if now - self.started >= self.limits.seconds:
            self.stop('duration_limit')
            raise Closed('duration_limit')
        if now - self.activity >= self.limits.idle_seconds:
            self.stop('idle_limit')
            raise Closed('idle_limit')
        try:
            permitted = self.allowed() is True
        except Exception:
            permitted = False
        if not permitted:
            self.stop('access_revoked')
            raise Closed('access_revoked')
        self.event_count += 1
        if self.event_count > self.limits.events:
            self.stop('event_limit')
            raise Closed('event_limit')

    def tick(self):
        """The enclosing transport must call this even when no events arrive."""
        self._guard()

    @staticmethod
    def _audio(value, cap):
        if not isinstance(value, str) or len(value) > ((cap + 2) // 3) * 4:
            raise ValueError('audio_invalid')
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('audio_invalid') from None
        if not raw or len(raw) > cap or len(raw) % 2 or base64.b64encode(raw).decode() != value:
            raise ValueError('audio_invalid')
        return raw

    def client(self, event):
        """Accept only PCM, bounded playback progress or end; never session updates/tools."""
        self._guard()
        if not isinstance(event, dict):
            raise ValueError('invalid_event')
        kind = event.get('type')
        if kind == 'audio.append' and set(event) == {'type', 'audio'}:
            raw = self._audio(event['audio'], self.limits.chunk_bytes)
            self.input_size += len(raw)
            if self.input_size > self.limits.input_bytes:
                self.stop('input_limit')
                raise Closed('input_limit')
            self.upstream({'type': 'input_audio_buffer.append', 'audio': event['audio']})
        elif kind == 'audio.played' and set(event) == {'type', 'item_id', 'audio_end_ms'}:
            elapsed = event['audio_end_ms']
            if event['item_id'] != self.item or type(elapsed) is not int or not self.played_ms <= elapsed <= self.sent_ms:
                raise ValueError('invalid_playback_progress')
            self.played_ms = elapsed
        elif kind == 'end' and set(event) == {'type'}:
            self.stop()
        else:
            raise ValueError('unsupported_client_event')

    def provider(self, event):
        """Translate only events required by this experiment; no tool dispatch."""
        self._guard()
        if not isinstance(event, dict):
            raise ValueError('invalid_event')
        kind = event.get('type')
        if kind == 'input_audio_buffer.speech_started':
            self.activity = self.clock()
            self.downstream({'type': 'speech_started'})
            if self.item is not None and self.item not in self.interrupted:
                self.interrupted.add(self.item)
                self.upstream({'type': 'conversation.item.truncate', 'item_id': self.item,
                               'content_index': 0, 'audio_end_ms': self.played_ms})
        elif kind == 'response.output_audio.delta':
            item = event.get('item_id')
            if not isinstance(item, str) or not item or len(item) > 200:
                raise ValueError('invalid_item')
            raw = self._audio(event.get('delta'), self.limits.chunk_bytes)
            self.output_size += len(raw)
            if self.output_size > self.limits.output_bytes:
                self.stop('output_limit')
                raise Closed('output_limit')
            if item in self.interrupted:
                return  # In-flight chunks after interruption must not play.
            if item != self.item:
                self.item, self.sent_ms, self.played_ms = item, 0, 0
            self.sent_ms += len(raw) * 1000 // 48_000  # PCM16 mono, 24 kHz.
            self.downstream({'type': 'audio.delta', 'item_id': item, 'audio': event['delta']})
        elif kind == 'conversation.item.input_audio_transcription.completed':
            text = event.get('transcript')
            if not isinstance(text, str) or len(text) > self.limits.transcript_chars:
                raise ValueError('invalid_transcript')
            self.downstream({'type': 'input_transcript', 'text': text, 'quality': 'automatic_unverified'})
        elif kind in {'response.done', 'error'}:
            self.downstream({'type': kind})
        else:
            raise ValueError('unsupported_provider_event')
