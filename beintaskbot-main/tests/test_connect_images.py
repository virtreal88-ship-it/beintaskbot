"""Synthetic inputs only; no OpenAI or production media requests."""
import asyncio,base64,copy,os
from unittest.mock import Mock,AsyncMock,patch
import pytest
from test_connect_voice import WorkerVoice,JOB,RESERVATION,CONFIG,row,CHANNEL,MESSAGE
import tenant_connect_images as photos
import tenant_ai_agent_worker as worker
import tenant_ai_agent_connect as adapter
from tenant_chat_ai_policy import history
from tenant_chat_ai_provider import generate
REAL_SUPPORTS_IMAGES=photos.supports_images
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6cN8AAAAASUVORK5CYII=')

@pytest.mark.parametrize('kind',['image','video','document'])
def test_unavailable_without_url_counted(kind):
 lines,missing=history([row(kind=kind,text='caption')],{})
 assert missing==1 and 'caption' in lines[0]['text'] and 'do not infer' in lines[0]['text']
 assert history([row(kind='image')],{},analyzed_media={MESSAGE})[1]==0

@pytest.mark.parametrize('model,expected',[('gpt-4o',True),('gpt-4o-mini',True),('gpt-4.1',True),('gpt-4.1-2025-04-14',True),('gpt-4.1-2025-04-14-fake',False),('gpt-4.1-2099-01-01',False),('gpt-4o-fake',False),('unknown',False)])
def test_exact_model_capabilities(model,expected):
 with patch.dict(os.environ,{},clear=True):assert photos.supports_images(model)==expected
 with patch.dict(os.environ,{'AI_AGENTS_VISION_MODELS':''}):assert not photos.supports_images(model)

class PhotoWorker(WorkerVoice):
 def setUp(self):
  super().setUp();self.rows=[row(kind='image',text='Synthetic error caption')]
  self.evidence={'status':'processed','code':'vision_observation','observation':'Visible error E17',
                 'observations':[{'origin':{},'text':'Visible error E17'}]}
 async def test_saved_observation_reaches_agent_without_resending_image_bytes(self):
  await self.process()
  assert 'Visible error E17' in str(self.generate.call_args.args[3])
  assert self.generate.call_args.kwargs['images']==[]
  assert self.generate.call_args.kwargs['extra_context']['unprocessed_media_count']==0
  self.prepare_media.assert_called_once();self.send.assert_called_once()
 async def test_failed_photo_is_explicit_without_invented_image_content(self):
  self.evidence={'status':'error','code':'media_unavailable'}
  await self.process()
  assert 'media_unavailable' in str(self.generate.call_args.args[3])
  assert self.generate.call_args.kwargs['extra_context']['unprocessed_media_count']==1
 async def test_manual_photo_remains_draft(self):
  self.reserve.side_effect=lambda *a,**k:{**RESERVATION,'mode':'manual'}
  await self.process();self.generate.assert_called_once();self.send.assert_not_called()
 async def test_group_local_replica_keeps_attribution(self):
  foreign={**row(mid='other-copy',kind='image'),'channel':CHANNEL,'photo_message_id':MESSAGE,
           'author_name':'Synthetic participant','source_channels':['one','two']}
  with patch.object(adapter,'group_context',return_value=[foreign]):await self.process()
  assert 'Visible error E17' in str(self.generate.call_args.args[3])
  assert self.generate.call_args.args[3][0]['sender']=='Synthetic participant'
 async def test_foreign_channel_cannot_reuse_local_message_receipt(self):
  foreign={**row(kind='image'),'channel':'different-channel'}
  with patch.object(adapter,'group_context',return_value=[foreign]):await self.process()
  assert 'Visible error E17' not in str(self.generate.call_args.args[3])
  assert self.generate.call_args.kwargs['extra_context']['unprocessed_media_count']==1

# Inherit no unrelated voice assertions in the photo-specific fixture.
for _name in list(WorkerVoice.__dict__):
 if _name.startswith('test_') and _name not in PhotoWorker.__dict__:setattr(PhotoWorker,_name,None)


@pytest.mark.parametrize('model',['gpt-4o-mini','gpt-4.1-2025-04-14'])
def test_provider_low_detail_is_transmitted(model):
 response=Mock(status_code=200);response.json.return_value={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'Synthetic answer'}]}]}
 with patch('tenant_chat_ai_provider.requests.post',return_value=response) as post:
  generate('synthetic-key',model,'reply',[],'',images=[{'content':PNG,'content_type':'image/png','detail':'low'}])
 assert post.call_args.args[0]=='https://api.openai.com/v1/responses'
 assert post.call_args.kwargs['json']['model']==model
 assert post.call_args.kwargs['json']['input'][0]['content'][1]['type']=='input_image'
 assert post.call_args.kwargs['json']['input'][0]['content'][1]['detail']=='low'


def test_secure_resolution_requires_exact_route_and_owned_message():
 from contextlib import contextmanager
 from unittest.mock import MagicMock
 conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value;conn.__enter__.return_value=conn
 route={'channel_id':CHANNEL,'conversation_id':JOB['conversation_id'],'api_key':'synthetic'}
 with patch.object(photos,'_connect',return_value=conn),patch.object(photos,'live',return_value=True),patch.object(photos,'connect_route',return_value=route):
  cur.fetchone.return_value={'ok':1};item=photos.resolve(JOB,RESERVATION,MESSAGE);assert item['message_id']==MESSAGE
  sql,args=cur.execute.call_args.args;assert JOB['tenant_id'] in args and 'connect:'+CHANNEL in args and 'connect:'+MESSAGE in args and "direction='incoming'" in sql
  cur.fetchone.return_value=None
  with pytest.raises(ValueError):photos.resolve(JOB,RESERVATION,MESSAGE)
 with patch.object(photos,'live',return_value=False):
  with pytest.raises(photos.ImageInactive):photos.resolve(JOB,RESERVATION,MESSAGE)

@pytest.mark.parametrize('case',['unknown','budget','oversize','mime','timeout'])
def test_image_limits_and_error_fallback(case):
 async def run():
  with patch.object(photos,'supports_images',return_value=case!='unknown'),patch.object(photos,'resolve',return_value={}),patch.object(photos,'live',return_value=True),patch.object(photos,'generation_budget_available',return_value=case!='budget'),patch.object(photos,'download_media',new=AsyncMock(return_value=(PNG,'video/mp4' if case=='mime' else 'image/png'))) as download,patch.object(photos,'MAX_TOTAL_BYTES',1 if case=='oversize' else 99999):
   if case=='timeout':download.side_effect=asyncio.TimeoutError()
   images,seen=await photos.prepare(JOB,RESERVATION,[row(kind='image')],'gpt-4o-mini',Mock());assert images==[] and not seen
   if case in ('unknown','budget'):download.assert_not_called()
 asyncio.run(run())


def test_live_requires_current_scope_and_group_fence():
 from unittest.mock import MagicMock
 import tenant_ai_agent_run_store as runs
 conn=MagicMock();conn.__enter__.return_value=conn
 with patch.object(photos,'active',return_value=True),patch.object(runs,'load_agent_config',return_value=CONFIG),patch.object(photos,'_connect',return_value=conn),patch.object(photos,'job_allowed',return_value=False):assert not photos.live(JOB,RESERVATION)
 with patch.object(photos,'active',return_value=True),patch.object(runs,'load_agent_config',return_value=None),patch.object(photos,'_connect') as db:
  assert not photos.live(JOB,RESERVATION);db.assert_not_called()


def test_no_unrelated_old_photos_or_photos_before_employee_reply():
 async def run():
  with patch.object(photos,'supports_images',return_value=True),patch.object(photos,'generation_budget_available',return_value=True),patch.object(photos,'download_media',new=AsyncMock()) as download:
   await photos.prepare(JOB,RESERVATION,[row(kind='image',at='2026-10-16T00:00:00Z'),row(kind='text',at='2026-10-16T01:00:00Z')],'gpt-4o',Mock())
   await photos.prepare(JOB,RESERVATION,[row(kind='image'),{**row(kind='text'),'direction':'outgoing'},row(kind='text')],'gpt-4o',Mock());download.assert_not_called()
 asyncio.run(run())


def _case_image_ref(message_id='case-photo-1',media_version='version-1'):
 from tenant_media_evidence import source_hash
 ref={'external_id':'connect:'+message_id,'channel_id':CHANNEL,'body_hash':'body-hash',
      'media_kind':'image','media_version':media_version}
 return {'message_id':message_id,'ref':ref,'source_hash':source_hash(ref),
         'case_id':'case-original','happened_at':'2026-10-01T00:00:00Z'}


def test_case_photo_before_old_outgoing_ack_is_selected_by_case_membership_without_recency_filter():
 async def run():
  selected=_case_image_ref()
  with patch.object(photos,'supports_images',return_value=True),patch.object(photos,'generation_budget_available',return_value=True),\
       patch.object(photos,'_case_image_refs',return_value=[selected]),\
       patch('tenant_media_evidence.reusable',return_value=None),patch.object(photos,'resolve',return_value={'synthetic':True}) as resolve,\
       patch.object(photos,'download_media',new=AsyncMock(return_value=(PNG,'image/png'))) as download,\
       patch.object(photos,'live',return_value=True):
   images,analyzed,artifacts=await photos.prepare_case(JOB,RESERVATION,'case-original','gpt-4o-mini',Mock())
  assert len(images)==1 and images[0]['message_id']=='case-photo-1'
  assert analyzed=={'case-photo-1'} and artifacts[0]['state']=='image_attached'
  resolve.assert_called_once_with(JOB,RESERVATION,'case-photo-1');download.assert_awaited_once()
 asyncio.run(run())


def test_case_image_reuses_exact_saved_receipt_without_media_download_or_analysis_call():
 async def run():
  selected=_case_image_ref();saved={'case_id':'case-original','message_id':'case-photo-1','relevance':'relevant',
      'text':'The screenshot shows a blank upload error panel.','content_sha256':'content-hash'}
  with patch.object(photos,'supports_images',return_value=True),patch.object(photos,'generation_budget_available',return_value=True),\
       patch.object(photos,'_case_image_refs',return_value=[selected]),patch('tenant_media_evidence.reusable',return_value={
         'status':'processed','result':{'case_observations':[saved]}}),patch.object(photos,'resolve') as resolve,\
       patch.object(photos,'download_media',new=AsyncMock()) as download:
   images,analyzed,artifacts=await photos.prepare_case(JOB,RESERVATION,'case-original','gpt-4o-mini',Mock())
  assert images==[] and analyzed=={'case-photo-1'}
  assert artifacts[0]['cached_observation']['relevance']=='relevant'
  assert artifacts[0]['analysis_expected'] is False
  resolve.assert_not_called();download.assert_not_awaited()
 asyncio.run(run())


def test_changed_case_media_source_hash_does_not_reuse_receipt():
 async def run():
  selected=_case_image_ref(media_version='replacement-version')
  previous=_case_image_ref(media_version='version-1')
  assert selected['source_hash']!=previous['source_hash']
  reusable=Mock(return_value=None)
  with patch.object(photos,'supports_images',return_value=True),patch.object(photos,'generation_budget_available',return_value=True),\
       patch.object(photos,'_case_image_refs',return_value=[selected]),patch('tenant_media_evidence.reusable',reusable),\
       patch.object(photos,'resolve',return_value={'synthetic':True}),patch.object(photos,'download_media',new=AsyncMock(return_value=(PNG,'image/png'))),\
       patch.object(photos,'live',return_value=True):
   images,_,artifacts=await photos.prepare_case(JOB,RESERVATION,'case-original','gpt-4o-mini',Mock())
  assert len(images)==1 and artifacts[0]['source_hash']==selected['source_hash']
  assert reusable.call_args.args[1]==selected['ref']
 asyncio.run(run())


def test_case_photo_unavailable_is_not_marked_analyzed_or_asserted_as_evidence():
 async def run():
  selected=_case_image_ref()
  with patch.object(photos,'supports_images',return_value=True),patch.object(photos,'generation_budget_available',return_value=True),\
       patch.object(photos,'_case_image_refs',return_value=[selected]),patch('tenant_media_evidence.reusable',return_value=None),\
       patch.object(photos,'resolve',side_effect=ValueError('synthetic unavailable')),patch.object(photos,'download_media',new=AsyncMock()) as download:
   images,analyzed,artifacts=await photos.prepare_case(JOB,RESERVATION,'case-original','gpt-4o-mini',Mock())
  assert images==[] and analyzed==set() and artifacts[0]['state']=='unavailable'
  assert artifacts[0]['analysis_expected'] is False;download.assert_not_awaited()
 asyncio.run(run())


def test_cached_legacy_cat_observation_cannot_be_promoted_to_new_visual_bug_evidence():
 import json
 async def run():
  selected=_case_image_ref()
  with patch.object(photos,'supports_images',return_value=True),patch.object(photos,'generation_budget_available',return_value=True),\
       patch.object(photos,'_case_image_refs',return_value=[selected]),patch('tenant_media_evidence.reusable',return_value={
         'status':'processed','result':{'observation':'The photo shows a cat on a chair.'}}),\
       patch.object(photos,'resolve') as resolve,patch.object(photos,'download_media',new=AsyncMock()) as download:
   images,_,artifacts=await photos.prepare_case(JOB,RESERVATION,'case-original','gpt-4o-mini',Mock())
  assert images==[] and artifacts[0]['analysis_expected'] is False
  assert artifacts[0]['cached_observation']['text']=='The photo shows a cat on a chair.'
  resolve.assert_not_called();download.assert_not_awaited()
  evidence=[{'speaker':'UNCLASSIFIED MEDIA NOTE','text':artifacts[0]['cached_observation']['text']}]
  assert not worker._pilot_quote_supported('The photo shows a cat on a chair.',
      [line for line in evidence if line['speaker']!='UNCLASSIFIED MEDIA NOTE'],[])
  fabricated={'action':'group_answer','message':'The photo confirms an upload defect.',
      'evidence_quote':'The photo confirms an upload defect.','media_observations':[
        {'message_id':'case-photo-1','relevance':'relevant','observation':'The photo confirms an upload defect.'}]}
  from tenant_consultation_group import parse_pilot_turn_decision
  assert parse_pilot_turn_decision(json.dumps(fabricated),expected_media_ids=[]) is None
  assert worker._pilot_case_media_quote_lines(fabricated['media_observations'],persisted_ids=[])==[]
 asyncio.run(run())


def test_irrelevant_case_photo_cannot_support_a_bug_claim():
 import json
 from tenant_consultation_group import parse_pilot_turn_decision
 observation='The photo shows a cat on a chair.'
 raw=json.dumps({'action':'group_answer','message':'This confirms the upload defect.',
   'evidence_quote':observation,'media_observations':[{'message_id':'case-photo-1','relevance':'irrelevant','observation':observation}]})
 decision=parse_pilot_turn_decision(raw,expected_media_ids=['case-photo-1'])
 assert decision and decision['media_observations'][0]['relevance']=='irrelevant'
 evidence=worker._pilot_case_media_quote_lines(decision['media_observations'])
 assert evidence==[] and not worker._pilot_quote_supported(observation,evidence,[])


def test_relevant_case_photo_can_support_only_its_validated_persisted_observation():
 import json
 from tenant_consultation_group import parse_pilot_turn_decision
 observation='The screenshot displays an upload failed banner.'
 raw=json.dumps({'action':'group_answer','message':'The screenshot reports an upload error.',
   'evidence_quote':observation,'media_observations':[{'message_id':'case-photo-1','relevance':'relevant','observation':observation}]})
 decision=parse_pilot_turn_decision(raw,expected_media_ids=['case-photo-1'])
 assert decision
 assert worker._pilot_case_media_quote_lines(decision['media_observations'])==[]
 lines=worker._pilot_case_media_quote_lines(decision['media_observations'],persisted_ids=['case-photo-1'])
 assert worker._pilot_quote_supported(observation,lines,[])
 assert worker._pilot_case_media_quote_lines(decision['media_observations'],persisted_ids=[])==[]
