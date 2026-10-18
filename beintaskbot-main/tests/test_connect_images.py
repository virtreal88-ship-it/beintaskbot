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
  self.stack.enter_context(patch.object(photos,'supports_images',return_value=True))
  self.stack.enter_context(patch.object(photos,'resolve',return_value={'synthetic':True}))
  self.stack.enter_context(patch.object(photos,'generation_budget_available',return_value=True))
  self.photo_live=self.stack.enter_context(patch.object(photos,'live',return_value=True))
  self.photo_download=self.stack.enter_context(patch.object(photos,'download_media',new=AsyncMock(return_value=(PNG,'image/png'))))
 async def test_photo_reaches_model_caption_author_and_low_budget(self):
  await self.process();kw=self.generate.call_args.kwargs
  assert len(kw['images'])==1 and kw['images'][0]['detail']=='low'
  assert kw['extra_context']['unprocessed_media_count']==0
  assert 'Synthetic error caption' in str(self.generate.call_args.args[3]);assert self.generate.call_args.args[3][0]['speaker']=='CLIENT'
  self.transcribe.assert_not_called();self.reserve.assert_called_once();self.send.assert_called_once()
 async def test_nizam_snapshot_uses_real_capability_gate(self):
  with patch.dict(os.environ,{},clear=True),patch.object(photos,'supports_images',side_effect=REAL_SUPPORTS_IMAGES),patch.dict(CONFIG,{'agent_model':'gpt-4.1-2025-04-14'}):
   await self.process()
  assert self.generate.call_args.args[1]=='gpt-4.1-2025-04-14'
  assert len(self.generate.call_args.kwargs['images'])==1
 async def test_unavailable_photo_explicit_and_no_images(self):
  self.photo_download.side_effect=ValueError('unavailable');await self.process()
  assert self.generate.call_args.kwargs['images']==[];assert self.generate.call_args.kwargs['extra_context']['unprocessed_media_count']==1
 async def test_human_takeover_after_download_no_model_or_send(self):
  self.photo_live.return_value=False;await self.process();self.generate.assert_not_called();self.send.assert_not_called();self.superseded.assert_called_once()
 async def test_manual_photo_keeps_draft(self):
  self.reserve.side_effect=lambda *a,**k:{**RESERVATION,'mode':'manual'};await self.process();self.send.assert_not_called();self.draft.assert_called_once()
 async def test_group_foreign_copy_resolves_only_local_replica(self):
  foreign={**row(mid='00000000-0000-0000-0000-000000000099',kind='image'), 'channel':CHANNEL,'photo_message_id':MESSAGE,'author_name':'Synthetic participant','source_channels':['one','two']}
  with patch.object(adapter,'group_context',return_value=[foreign]):await self.process()
  assert photos.resolve.call_args.args[2]==MESSAGE
  assert self.generate.call_args.args[3][0]['sender']=='Synthetic participant'
  assert self.generate.call_args.kwargs['extra_context']['unprocessed_media_count']==0
 async def test_foreign_only_photo_not_downloaded(self):
  foreign={**row(kind='image'),'channel':'00000000-0000-0000-0000-000000000088'}
  with patch.object(adapter,'group_context',return_value=[foreign]):await self.process()
  self.photo_download.assert_not_called();assert self.generate.call_args.kwargs['extra_context']['unprocessed_media_count']==1
 async def test_three_photos_bounded_two_and_deduplicated(self):
  context=[row(mid=str(i),kind='image') for i in range(3)]
  with patch.object(adapter,'group_context',return_value=context):await self.process()
  assert self.photo_download.await_count==2;assert len(self.generate.call_args.kwargs['images'])==1;assert self.generate.call_args.kwargs['extra_context']['unprocessed_media_count']==1

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
