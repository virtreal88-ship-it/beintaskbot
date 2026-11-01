"""Synthetic Fəxri-shaped evidence only; no media/provider/database calls."""
from unittest.mock import Mock
import pytest
import tenant_episode_media as episode
import tenant_media_evidence as evidence
from tenant_operator_copilot import _bounded_conversation, INSTRUCTIONS
from tenant_dialog_evidence import GROUNDING_RULE
from test_episode_media import row, Authority


class SavedCursor:
    def __init__(self, saved): self.saved = saved
    def execute(self, sql, params=None): self.sql = sql
    def fetchone(self):
        return {'relation': True} if 'to_regclass' in self.sql else self.saved


def annotate(item, text, *, kind='image', complete=True):
    saved={'status':'processed' if complete else 'partial','code':'vision_observation',
           'coverage':{'kind':kind,'complete':complete},
           'result':{'observations':[{'origin':{},'text':text}]}}
    evidence.annotate_rows(SavedCursor(saved),'tenant',[item])
    return item


@pytest.mark.parametrize('new_topic', [False, True])
def test_cached_photos_caption_staff_and_followup_are_distinct_no_paid_io(monkeypatch,new_topic):
    caption='bu mehsul admin listesinde tapilmir ve qiymetini düzelde bilmirik'
    product=annotate(row('image',1,channel='connect:channel',body=caption),
        'KIRLANGIÇ sunflower oil 1L. Barcode: 4006381333931. Phone: +994501234567. password=private')
    remote=annotate(row('image',2,channel='connect:channel'),
        'AnyDesk window: Free license (non-professional use). Your Address: 123456789')
    staff=row('text',3,body='Product was not created',raw={'verified_employee_id':7})
    voice=row('voice',4,body='STALE LEGACY AUDIO')
    text='New topic: receipt printer has no paper' if new_topic else 'Why is this product in POS but missing in administrator?'
    rows=[product,remote,staff,voice]
    monkeypatch.setattr(episode,'reusable',Mock(return_value={'status':'processed','code':'transcribed',
        'coverage':{'kind':'voice','complete':True},'result':{'transcript':text}}))
    flight=Mock(side_effect=AssertionError('No new analysis allowed'))
    monkeypatch.setattr(episode,'_flight',flight)
    monkeypatch.setattr('tenant_knowledge_media.collect',Mock(side_effect=AssertionError('No provider allowed')))
    auth=Authority(rows);auth.latest_request=True
    episode.prepare(auth,rows)
    assert episode.episode(rows,latest_request=True)==[voice]
    for lines in (_bounded_conversation(rows), episode.context_lines(rows,rows)[0]):
        product_line,remote_line,staff_line,voice_line=lines
        assert product_line['text'].count(caption)==1
        assert 'KIRLANGIÇ' in product_line['text']
        assert product_line['evidence_scope']=='historical'
        assert product_line['provenance']['relationship_to_latest_request']=='must_be_supported_by_customer_followup'
        assert product_line['provenance']['numeric_identifiers'].startswith('redacted_unknown_type')
        assert '4006381333931' not in str(lines) and '994501234567' not in str(lines)
        assert '123456789' not in str(lines) and 'private' not in str(lines)
        assert 'AnyDesk' in remote_line['text'] and 'Free license (non-professional use)' in remote_line['text']
        assert staff_line['speaker']=='EMPLOYEE'
        assert staff_line['provenance']['statement_origin']=='employee_statement_unverified'
        assert text in voice_line['text'] and voice_line['evidence_scope']=='current_episode'
        assert 'STALE LEGACY AUDIO' not in str(lines)
        assert 'sampled video' not in str(lines)
    flight.assert_not_called()
    assert sum(line['saved_visual_evidence'] for line in _bounded_conversation(rows))==2
    assert not auth.calls


@pytest.mark.parametrize('kind',['image','document','video'])
def test_caption_without_receipt_survives_but_is_not_analysis(monkeypatch,kind):
    photo=row(kind,1,body='The price cannot be changed')
    rows=[photo,row('text',2,direction='outgoing'),row('text',3,body='Followup')]
    monkeypatch.setattr(episode,'_flight',Mock(side_effect=AssertionError('historical')))
    episode.prepare(Authority(rows),rows)
    lines=_bounded_conversation(rows)
    assert lines[0]['text'].count('The price cannot be changed')==1
    assert 'not extracted media evidence' in lines[0]['text']
    assert 'not evidence for the current request' in lines[0]['text']
    assert '_episode_receipt' not in photo


@pytest.mark.parametrize('kind,complete,expected',[
    ('image',True,'Saved visual observation'),('image',False,'Partial visual observation'),
    ('video',False,'Partial sampled video observation'),('document',False,'Partial document text')])
def test_saved_observation_modality(kind,complete,expected):
    item=annotate(row(kind,channel='connect:channel'),'Unreadable barcode',kind=kind,complete=complete)
    assert expected in item['media_evidence_context']
    assert item['media_evidence']['coverage']['complete'] is complete
    if kind!='video':assert 'video' not in item['media_evidence_context']


@pytest.mark.parametrize('text',[
    'AnyDesk: Free license (non-professional use). BeinSystems window behind it.',
    'AnyDesk license expired. BeinSystems window has no license warning.',
    'BeinSystems window: license expired. AnyDesk: Free license (non-professional use).',
    'Unidentified window: license expired. Application name unreadable.',
])
def test_observation_attribution_preserved_without_cross_window_inference(text):
    item=annotate(row('image',channel='connect:channel'),text)
    assert text in item['media_evidence_context']
    lines=_bounded_conversation([item])
    assert lines[0]['source_id']==item['external_id']
    assert lines[0]['provenance']['application_identity']=='only_the_window_explicitly_identified_in_this_observation'
    assert GROUNDING_RULE in INSTRUCTIONS
    assert 'free/non-professional license is not evidence of expiration' in GROUNDING_RULE.lower()


def test_external_redaction_unchanged_even_for_checksum_valid_barcode():
    for value in ('4006381333931','+994501234567','123456789'):
        assert value not in evidence._safe_text('Barcode / phone / remote ID: '+value)
    assert 'credential' not in evidence._safe_text('password=credential')
