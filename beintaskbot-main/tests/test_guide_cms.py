import unittest,tempfile,json,threading,copy,zipfile,io,asyncio
from pathlib import Path
from types import SimpleNamespace
from guide_cms import GuideCMS,validate_article,export_zip
class Storage:
 def __init__(self):self._cache={};self._lock=threading.Lock();self.ok=True
 def _load_file(self,name):return self._cache.get(name,{})
 def _save_file(self,name):return self.ok
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);(self.base/'guide_site').mkdir()
  (self.base/'guide_site/seed.json').write_text('[]')
  self.storage=Storage();self.cms=GuideCMS(self.base,lambda actor:actor==1,self.storage)
  self.data={'slug':'/articles/test','title':'Title','body':'<script>alert(1)</script>','category':'products','version':0,'action':'draft'}
 def tearDown(self):self.temp.cleanup()
 def test_draft_is_not_exported(self):
  doc=self.cms.save(self.data,1);self.assertIsNone(doc['items'][0]['published'])
 def test_ready_then_draft_keeps_export_version(self):
  ready=self.cms.save({**self.data,'action':'ready'},1)
  doc=self.cms.save({**self.data,'version':ready['version'],'body':'Edited draft'},1)
  self.assertEqual(doc['items'][0]['published']['body'],self.data['body'])
 def test_conflict_does_not_overwrite(self):
  self.cms.save(self.data,1)
  with self.assertRaises(FileExistsError):self.cms.save(self.data,1)
  self.assertEqual(self.cms.read()['version'],1)
 def test_storage_failure_rolls_back(self):
  self.storage.ok=False
  with self.assertRaises(RuntimeError):self.cms.save(self.data,1)
  self.assertEqual(self.cms.read()['version'],0)
 def test_bad_slug_rejected(self):
  for slug in ['/../private','https://a.com','//host','/a/','/a?b']:
   with self.assertRaises(ValueError):validate_article({**self.data,'slug':slug})
 def test_anonymous_and_unprivileged_denied(self):
  for actor in [0,2]:
   request={'authenticated_chat_id':actor}
   self.assertEqual(asyncio.run(self.cms.api(request)).status,403)
   self.assertEqual(asyncio.run(self.cms.export(request)).status,403)
 def test_zip_only_public_fields_and_safe_names(self):
  for name in ['index.html','guide-articles.js']:(self.base/'guide_site'/name).write_text('test')
  result=export_zip(self.base,[{**self.data,'updated_by':'private'}])
  with zipfile.ZipFile(io.BytesIO(result)) as archive:
   self.assertEqual(set(archive.namelist()),{'index.html','guide-articles.js','guide-articles.json','UPLOAD.txt'})
   payload=json.loads(archive.read('guide-articles.json'))
   self.assertNotIn('updated_by',payload['items'][0]);self.assertNotIn('version',payload['items'][0])
if __name__=='__main__':unittest.main()
