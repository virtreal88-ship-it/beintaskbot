"""No provider or media I/O: receipt scope and terminal-state regressions."""
import tenant_media_evidence as evidence


class Cursor:
    def __init__(self): self.calls=[]
    def execute(self, query, params): self.calls.append((query,params))


def _ref(channel='00000000-0000-0000-0000-000000000001', external='connect:one'):
    return {'id':'message-row','channel_id':channel,'external_id':external,'body_hash':'body-v1',
            'media_kind':'video','role':'operator'}


def test_receipt_version_and_channel_are_part_of_reuse_key():
    base=evidence.source_hash(_ref())
    assert base != evidence.source_hash({**_ref(),'body_hash':'body-v2'})
    assert base != evidence.source_hash({**_ref(),'external_id':'connect:two'})
    cursor=Cursor()
    evidence.persist(cursor,'00000000-0000-0000-0000-000000000010',[_ref(channel='00000000-0000-0000-0000-000000000002')],
                     [{'id':'message-row','status':'unavailable','code':'scope','kind':'video'}])
    assert cursor.calls[0][1][1]=='00000000-0000-0000-0000-000000000002'


def test_persist_marks_video_sample_as_partial_not_complete():
    cursor=Cursor();ref=_ref()
    evidence.persist(cursor,'00000000-0000-0000-0000-000000000010',[ref],[{
        'id':'message-row','status':'partial','code':'vision_observation','kind':'video',
        'visuals':[{'second':0}], 'transcript':'safe evidence'}])
    assert len(cursor.calls)==1
    params=cursor.calls[0][1]
    assert '"complete":false' in params[8].replace(' ','').lower()
    assert 'safe evidence' in params[9]


def test_reused_partial_is_explicit_and_never_claimed_complete():
    message={'text':'caption'}
    result=evidence.apply_reusable(message,{'status':'partial','code':'vision_observation',
        'coverage':{'complete':False},'result':{'observation':'frame detail'}})
    assert result['status']=='partial' and result['coverage']['complete'] is False
    assert 'partial sampled video' in message['text']
