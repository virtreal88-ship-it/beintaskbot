const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.join(__dirname,'..'),read=p=>fs.readFileSync(path.join(root,p),'utf8');
const settle=async()=>{for(let i=0;i<30;i++)await new Promise(r=>setImmediate(r));};
(async()=>{
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];
 const legacy={model:'legacy-text',transcription_model:'whisper-1',copilot_model:'legacy-copilot',photo_model:'gpt-4.1',video_model:'gpt-4o-mini',video_enabled:false};
 let state={success:true,configured:true,enabled:true,consent:true,audio_enabled:true,copilot_enabled:true,copilot_daily_limit:50,daily_limit:50,media_hosts:[],updated_at:'v1',...legacy};
 w.tenantI18n={language:'ru'};w.tenantSharedIntegrations={scoped:()=>false,can:()=>true,connected:()=>false,command:(_data,_provider,command)=>command,record:()=>{},row:()=>({management_actions:{}})};let navigation='';w.tenantAppNavigate=view=>{navigation=view;};
 w.fetch=async(url,opts={})=>{const body=opts.body?JSON.parse(opts.body):null;calls.push({url:String(url),body});let payload;
 if(url==='/api/platform/me')payload={member:{tenant_id:'tenant-1',telegram_id:7,role:'owner'}};
 else if(url==='/api/platform/chat/ai/recorded-voice-access')payload={success:true,http_status:200,api_voices_read_verified:true,recorded_voice_creation_access:'not_checked'};
 else {assert.equal(url,'/api/platform/chat/ai/settings');if(body)state={...state,...body,updated_at:'v2'};payload=state;}
 return{ok:true,status:200,json:async()=>payload};};
 w.eval(read('docs/tenant-view-demand.js'));w.eval(read('docs/tenant-chat-ai-settings.js'));await settle();d.dispatchEvent(new w.CustomEvent('tenant-integration-panel',{detail:{provider:'openai',open:true}}));await settle();
 const panel=d.querySelector('#tcAiPanel');assert(panel);assert.equal(panel.querySelectorAll('select').length,0);
 assert(d.getElementById('tcAiVoiceCheck'),'owner sees the recorded voice access check');
 for(const id of ['tcCopilotModel','tcAiModel','tcAiTranscription','tcPhotoModel','tcVideoModel','tcCopilotProbe'])assert.equal(d.getElementById(id),null);
 assert.match(panel.textContent,/задаются у агента/);d.getElementById('tcAgentSettings').click();assert.equal(navigation,'ai_agents');
 d.getElementById('tcAiVoiceCheck').click();await settle();
 const voiceCall=calls.find(c=>c.url==='/api/platform/chat/ai/recorded-voice-access');assert(voiceCall);assert.deepEqual(voiceCall.body,{expected_tenant_id:'tenant-1',expected_user_id:7});
 assert.equal(Object.hasOwn(voiceCall.body,'api_key'),false);assert.match(d.getElementById('tcAiVoiceNotice').textContent,/HTTP 200/);assert.match(d.getElementById('tcAiVoiceNotice').textContent,/Создание голоса не проверялось/);
 d.getElementById('tcCopilotLimit').value='17';d.getElementById('tcAiSave').click();await settle();
 const body=calls.find(c=>c.url==='/api/platform/chat/ai/settings'&&c.body).body;assert.equal(body.preserve_models,true);assert.equal(body.copilot_daily_limit,17);assert.equal(body.expected_updated_at,'v1');assert.equal(body.expected_tenant_id,'tenant-1');assert.equal(body.expected_user_id,7);assert.equal(body.api_key,'');assert.equal(body.remove_key,false);
 for(const key of Object.keys(legacy)){assert.equal(Object.hasOwn(body,key),false);assert.equal(state[key],legacy[key]);}
 assert.equal(calls.filter(c=>/probe|models|send|transcrib/.test(c.url)).length,0);
 dom.window.close();console.log('PASS company AI access: no model selectors, assigned-agent navigation, scoped limits save and no model/credential/processing mutations.');
})().catch(error=>{console.error(error);process.exitCode=1;});
