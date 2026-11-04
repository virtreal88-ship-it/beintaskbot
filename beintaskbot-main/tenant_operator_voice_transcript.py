"""Ordered, text-only transcript projection for one server-owned voice session."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import math
import uuid


TRANSCRIPTION_MODEL = 'gpt-transcribe'
TRANSCRIPTION_LANGUAGES = ['ru', 'az']
MAX_SESSION_SECONDS = 120
TRANSCRIPTION_MICROUSD_PER_MINUTE = 4_500


def transcription_cost_microusd(usage):
    """Price a trusted duration receipt conservatively; unknown shapes stay unknown."""
    if not isinstance(usage, dict) or set(usage) != {'type', 'seconds'} or usage.get('type') != 'duration':
        return None
    seconds = usage.get('seconds')
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    if isinstance(seconds, float) and not math.isfinite(seconds):
        return None
    try:
        duration = Decimal(str(seconds))
    except (InvalidOperation, ValueError):
        return None
    if not duration.is_finite() or duration < 0 or duration > MAX_SESSION_SECONDS:
        return None
    return int((duration * TRANSCRIPTION_MICROUSD_PER_MINUTE / 60).to_integral_value(rounding=ROUND_CEILING))


class TranscriptDraft:
    """Correlate exact provider item IDs while preserving creation order.

    No audio is retained. Completed text is labeled automatic/unverified; a
    client playback acknowledgment never becomes proof that a person heard it.
    """
    def __init__(self, session_id, agent_binding):
        self.session_id = str(uuid.UUID(session_id))
        if not isinstance(agent_binding, str) or not 1 <= len(agent_binding) <= 128:
            raise ValueError('binding_invalid')
        self.binding = agent_binding
        self.turns = []
        self.items = {}
        self.assistant_state = {}
        self.closed = False

    def register(self, item_id, role):
        if (self.closed or role not in ('employee', 'copilot') or not isinstance(item_id, str) or
                not item_id or len(item_id) > 128):
            raise ValueError('turn_invalid')
        old = self.items.get(item_id)
        if old is not None:
            if old['role'] == role:
                return 'duplicate'
            raise ValueError('turn_conflict')
        if len(self.turns) >= 20:
            raise ValueError('turn_limit')
        turn = {'role': role, 'text': '', 'agent_binding': self.binding,
                'voice_session_id': self.session_id, 'turn_sequence': len(self.turns),
                'transcript_status': 'unavailable', 'transcript_quality': 'unknown'}
        self.items[item_id] = turn
        self.turns.append(turn)
        if role == 'copilot':
            self.assistant_state[item_id] = {'text': None, 'audio_done': False,
                                             'played': False, 'interrupted': False}
        return 'registered'

    def input_completed(self, item_id, content_index, text):
        turn = self.items.get(item_id)
        if (self.closed or turn is None or turn['role'] != 'employee' or type(content_index) is not int or
                content_index != 0 or not isinstance(text, str) or len(text) > 1600):
            raise ValueError('transcript_invalid')
        if turn['transcript_quality'] == 'automatic_unverified':
            if turn['text'] == text and turn['transcript_status'] == ('complete' if text.strip() else 'unavailable'):
                return 'duplicate'
            raise ValueError('transcript_conflict')
        if turn['transcript_quality'] != 'unknown':
            raise ValueError('transcript_invalid')
        if text.strip():
            turn.update(text=text, transcript_status='complete', transcript_quality='automatic_unverified')
        else:
            turn.update(transcript_status='unavailable', transcript_quality='automatic_unverified')
        return 'completed'

    def input_failed(self, item_id, content_index):
        turn = self.items.get(item_id)
        if (self.closed or turn is None or turn['role'] != 'employee' or type(content_index) is not int or
                content_index != 0):
            raise ValueError('transcript_invalid')
        if turn['transcript_quality'] == 'automatic_unverified' and turn['transcript_status'] == 'unavailable':
            return 'duplicate'
        if turn['transcript_quality'] != 'unknown':
            raise ValueError('transcript_conflict')
        turn.update(transcript_status='unavailable', transcript_quality='automatic_unverified')
        return 'failed'

    def assistant_transcript_completed(self, item_id, text):
        state = self.assistant_state.get(item_id)
        if (self.closed or state is None or state['interrupted'] or not isinstance(text, str) or
                not text.strip() or len(text) > 6000):
            raise ValueError('transcript_invalid')
        if state['text'] is not None:
            if state['text'] == text:
                return 'duplicate'
            raise ValueError('transcript_conflict')
        state['text'] = text
        return 'completed'

    def assistant_audio_done(self, item_id):
        state = self.assistant_state.get(item_id)
        if self.closed or state is None or state['interrupted']:
            raise ValueError('audio_state_invalid')
        if state['audio_done']:
            return 'duplicate'
        state['audio_done'] = True
        return 'done'

    def device_playback_complete(self, item_id):
        state = self.assistant_state.get(item_id)
        if self.closed or state is None or not state['audio_done'] or state['interrupted']:
            raise ValueError('playback_state_invalid')
        if state['played']:
            return 'duplicate'
        state['played'] = True
        return 'completed'

    def interrupted(self, item_id):
        state = self.assistant_state.get(item_id)
        if self.closed or state is None:
            raise ValueError('audio_state_invalid')
        if state['interrupted']:
            return 'duplicate'
        state['interrupted'] = True
        state['played'] = False
        state['text'] = None
        return 'interrupted'

    def finalize(self):
        if self.closed:
            raise ValueError('already_finalized')
        self.closed = True
        for item_id, state in self.assistant_state.items():
            if state['played'] and not state['interrupted'] and state['text'] is not None:
                turn = self.items[item_id]
                turn.update(text=state['text'], transcript_status='complete',
                            transcript_quality='automatic_unverified')
            state['text'] = None
        return [dict(turn) for turn in self.turns]
