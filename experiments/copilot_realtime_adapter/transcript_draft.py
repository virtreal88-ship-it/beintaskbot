"""Synthetic transcript lifecycle experiment; never wired to live sessions.

The experiment stores text only, in memory, and produces candidate thread
entries. It does not enable ASR, authorize storage or append to the database.
"""
import uuid
from tenant_operator_copilot_threads import _validated_entries


class TranscriptDraft:
    def __init__(self,session_id,agent_binding):
        self.session_id=str(uuid.UUID(session_id))
        if not isinstance(agent_binding,str) or not 1<=len(agent_binding)<=128:raise ValueError('binding_invalid')
        self.binding=agent_binding;self.turns=[];self.items={};self.assistant_state={};self.playback_evidence={};self.closed=False

    def register(self,item_id,role):
        # Called on ordered provider item creation, NOT on late ASR completion.
        if (self.closed or role not in ('employee','copilot') or not isinstance(item_id,str) or
            not item_id or len(item_id)>128 or item_id in self.items or len(self.turns)>=20):
            raise ValueError('turn_invalid')
        turn={'role':role,'text':'','agent_binding':self.binding,'voice_session_id':self.session_id,
              'turn_sequence':len(self.turns),'transcript_status':'unavailable','transcript_quality':'unknown'}
        self.items[item_id]=turn;self.turns.append(turn)
        if role=='copilot':self.assistant_state[item_id]={'text':None,'audio_done':False,'played':False,'interrupted':False}

    def assistant_transcript_completed(self,item_id,text):
        state=self.assistant_state.get(item_id)
        if (self.closed or state is None or state['interrupted'] or state['text'] is not None or not isinstance(text,str) or
            not text.strip() or len(text)>6000):raise ValueError('transcript_invalid')
        state['text']=text

    def assistant_audio_done(self,item_id):
        state=self.assistant_state.get(item_id)
        if self.closed or state is None or state['audio_done'] or state['interrupted']:raise ValueError('audio_state_invalid')
        state['audio_done']=True

    def device_playback_complete(self,item_id):
        # A trusted app-level queue signal only, NOT proof that a person heard
        # anything. The real client path and auth are not integrated here.
        state=self.assistant_state.get(item_id)
        if self.closed or state is None or not state['audio_done'] or state['played'] or state['interrupted']:
            raise ValueError('playback_state_invalid')
        state['played']=True

    def interrupted(self,item_id):
        state=self.assistant_state.get(item_id)
        if self.closed or state is None or state['interrupted']:raise ValueError('audio_state_invalid')
        state['interrupted']=True;state['played']=False;state['text']=None

    def input_completed(self,item_id,content_index,text):
        turn=self.items.get(item_id)
        if (self.closed or turn is None or turn['role']!='employee' or type(content_index) is not int or content_index!=0 or
            turn['transcript_quality']!='unknown' or not isinstance(text,str) or len(text)>1600):
            raise ValueError('transcript_invalid')
        if text.strip():
            turn.update(text=text,transcript_status='complete',transcript_quality='automatic_unverified')
        else:
            # A final blank result is unknown content, never proof of silence.
            turn.update(transcript_status='unavailable',transcript_quality='automatic_unverified')

    def input_failed(self,item_id,content_index):
        turn=self.items.get(item_id)
        if (self.closed or turn is None or turn['role']!='employee' or type(content_index) is not int or content_index!=0 or
            turn['transcript_quality']!='unknown'):raise ValueError('transcript_invalid')
        turn.update(transcript_status='unavailable',transcript_quality='automatic_unverified')

    def finalize(self):
        if self.closed:raise ValueError('already_finalized')
        self.closed=True
        # Only a complete transcript and an app-level full playback signal may
        # enter this mock projection. This signal cannot prove human hearing or
        # resist a malicious client; the evidence is separate from DB entries.
        for item,state in self.assistant_state.items():
            if state['played'] and not state['interrupted'] and state['text'] is not None:
                turn=self.items[item]
                turn.update(text=state['text'],transcript_status='complete',transcript_quality='automatic_unverified')
                self.playback_evidence[turn['turn_sequence']]='device_playback_complete'
            state['text']=None
        entries=[dict(turn) for turn in self.turns]
        return _validated_entries(self.binding,entries) if entries else []
