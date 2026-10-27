const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const script=fs.readFileSync(path.join(__dirname,'../docs/tenant-chat-ai-settings.js'),'utf8');
const settle=async()=>{for(let i=0;i<20;i++)await new Promise(r=>setImmediate(r));};
(async()=>{
 for(const lang of ['ru','az','en']){
  const dom=new JSDOM('<div id="view-settings"></div>',{url:'https://crm.test/app',runScripts:'outside-only'});
  const w=dom.window,d=w.document,calls=[];w.tenantI18n={language:lang};
  const original={success:true,configured:true,enabled:true,consent:true,audio_enabled:true,
   model:'old-text',transcription_model:'old-asr',copilot_model:'old-copilot',copilot_enabled:true,
   daily_limit:50,copilot_daily_limit:50,media_hosts:['media.example.test'],updated_at:'v1',photo_model:'',
   photo_model_tariffs:{'gpt-4.1':{ready:true,max_microusd:8896}}};
  let state={...original};
  w.fetch=async(url,options={})=>{
   const body=options.body?JSON.parse(options.body):null;calls.push({url,body});
   let result;
   if(url==='/api/platform/me')result={member:{tenant_id:'a',telegram_id:11,role:'owner'}};
   else {assert.equal(url,'/api/platform/chat/ai/settings');if(body)state={...state,...body,updated_at:'v2'};result=state;}
   return {ok:true,json:async()=>result};
  };
  w.eval(script);await settle();
  const select=d.getElementById('tcPhotoModel');assert.equal(select.value,'');assert.equal(select.disabled,false);
  select.value='gpt-4.1';select.onchange();assert.match(d.getElementById('tcPhotoStatus').textContent,/0\.008896/);
  d.getElementById('tcAiSave').click();await settle();
  const saved=calls.find(x=>x.body).body;
  assert.equal(saved.photo_model,'gpt-4.1');assert.equal(saved.api_key,'');assert.equal(saved.remove_key,false);
  for(const field of ['model','transcription_model','copilot_model','daily_limit','copilot_daily_limit','audio_enabled'])assert.equal(saved[field],original[field]);
  assert.equal(saved.expected_tenant_id,'a');assert.equal(saved.expected_user_id,11);assert.equal(saved.expected_updated_at,'v1');
  state={...state,photo_model:'',photo_model_tariffs:{}};d.getElementById('tcAiLoad').click();await settle();
  d.getElementById('tcPhotoModel').value='gpt-4.1';d.getElementById('tcAiSave').click();await settle();
  assert.equal(calls.filter(x=>x.body).length,1);assert.match(d.getElementById('tcAiNotice').textContent,/photo_model_tariff_required/);
  d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'b',telegram_id:22,role:'admin'}}}));await settle();
  assert.equal(d.getElementById('tcPhotoModel').disabled,true);assert.equal(d.getElementById('tcPhotoModel').value,'');
  assert(calls.every(x=>!x.url.includes('probe')&&!x.url.includes('send')&&!x.url.includes('consent')));
  dom.window.close();
 }
 console.log('PASS photo settings RU/AZ/EN: owner selection, tariff before save, independent text/ASR, company reset; no provider calls.');
})().catch(error=>{console.error(error);process.exitCode=1;});
