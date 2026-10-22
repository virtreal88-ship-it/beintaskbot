const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.join(__dirname,'..');
const read=file=>fs.readFileSync(path.join(root,file),'utf8');
const baseline=file=>execFileSync('git',['show','ccca01ee:beintaskbot-main/'+file],{cwd:root,encoding:'utf8'});
const settle=async()=>{for(let i=0;i<15;i++)await new Promise(r=>setImmediate(r));};
const A='00000000-0000-0000-0000-000000000101',C='00000000-0000-0000-0000-000000000201';
const pic=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7l8AAAAASUVORK5CYII=','base64');
const sound=Buffer.alloc(1644);sound.write('RIFF');sound.writeUInt32LE(1636,4);sound.write('WAVEfmt ',8);sound.writeUInt32LE(16,16);sound.writeUInt16LE(1,20);sound.writeUInt16LE(1,22);sound.writeUInt32LE(8000,24);sound.writeUInt32LE(16000,28);sound.writeUInt16LE(2,32);sound.writeUInt16LE(16,34);sound.write('data',36);sound.writeUInt32LE(1600,40);
async function setup(previous=false,group=false,shared=false){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app/dialogs?site_lang=ru',runScripts:'outside-only'}),w=dom.window,calls=[],revoked=[],errors=[];
 w.HTMLMediaElement.prototype.pause=function(){Object.defineProperty(this,'paused',{value:true,configurable:true});this.dispatchEvent(new w.Event('pause'));};w.HTMLMediaElement.prototype.play=async function(){Object.defineProperty(this,'paused',{value:false,configurable:true});this.dispatchEvent(new w.Event('play'));};
 w.addEventListener('error',e=>errors.push(e.error));w.Blob=Blob;w.AbortController=AbortController;w.URL.createObjectURL=()=> 'blob:test/'+Math.random();w.URL.revokeObjectURL=v=>revoked.push(v);
 const member={tenant_id:A,telegram_id:33,role:'worker',permissions:['dialogs'],active:true};
 const profile={member,capabilities:{modules:{dialogs:true}}};w.tenantProfileSnapshot=profile;
 const route={provider:'connect',lead_id:41,channel_id:C,conversation_id:group?'group@g.us':'direct@s.whatsapp.net',customer_kind:group?'group':'contact',customer_name:group?'Test group':'Test customer',avatar_status:'',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:false};
 const messages=[{external_id:'connect:00000000-0000-0000-0000-000000000501',source:'connect',direction:'incoming',message_type:'image',body:'Photo',author_type:group?'participant':'customer',author_name:'Alice'},
 {external_id:'connect:00000000-0000-0000-0000-000000000502',source:'connect',direction:'outgoing',message_type:'audio',body:'Salam! Привет!',duration_seconds:42,...(previous?{}:{has_transcription:true}),author_type:'operator',author_name:'Operator'}];
 let unavailable=false,mediaBytes=0,jsonBytes=0;
 w.fetch=async(url,options={})=>{
  calls.push({url:String(url),method:options.method||'GET'});let value;
  if(String(url).includes('/crm/chat/attachment')){
   const query=new URL(String(url),'https://test.invalid').searchParams;assert.equal(query.get('channel_id'),C);assert.equal(query.get('conversation_id'),route.conversation_id);
   if(unavailable)return {ok:false,status:404,json:async()=>({error:'not available'})};
   const audio=String(url).includes('000502'),bytes=audio?sound:pic;mediaBytes+=bytes.length;
   let sent=false;return {ok:true,headers:new Headers({'Content-Type':audio?'audio/wav':'image/png','Content-Length':String(bytes.length)}),body:{getReader:()=>({read:async()=>sent?{done:true}:(sent=true,{done:false,value:new Uint8Array(bytes)}),cancel:async()=>{}})}};
  }
  if(String(url).includes('/api/platform/me'))value=profile;
  else if(String(url).includes('/dialogs/history'))value={success:true,result:{dialog:route,messages,next_before:shared?'older':null}};
  else if(String(url).includes('/dialogs'))value={success:true,result:{dialogs:[route],channels:[],agents:[],attention_count:1,next_offset:null}};
  else throw Error('Unexpected side effect: '+url);
  jsonBytes+=Buffer.byteLength(JSON.stringify(value));return {ok:true,json:async()=>value};
 };
 for(const file of ['docs/tenant-operator-i18n.js','docs/tenant-chat-attachments.js',...(shared?['docs/tenant-chat-ai.js','docs/tenant-chat-history.js','docs/tenant-chat-import.js']:[]),'docs/tenant-operator-dialogs.js'])w.eval(previous?baseline(file):read(file));
 w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));await settle();
 const listCalls=calls.length,listBytes=jsonBytes;
 assert.equal(w.document.querySelectorAll('#operatorRows .operator-row-avatar-image').length,0,'unattempted history is not loaded from the list');
 await w.tenantOperator.openDialog(route);
 const avatar=w.document.querySelector('#operatorConversationHeader .operator-header-avatar');if(!previous){assert.ok(avatar,'opening the selected dialog starts the one-time avatar request');assert.ok(avatar.querySelector('img')?.getAttribute('src').includes('dialogs/avatar'));assert.ok(avatar.querySelector('img')?.getAttribute('src').includes(C));}await settle();
 return {dom,w,calls,revoked,errors,route,profile,listCalls,listBytes,historyCalls:calls.length-listCalls,historyBytes:jsonBytes-listBytes,get mediaBytes(){return mediaBytes},setUnavailable:()=>unavailable=true};
}
(async()=>{
 const shared=await setup(false,true,true);assert.deepEqual(shared.errors,[]);
 await shared.w.document.getElementById('operatorEarlier').onclick();await settle();assert.deepEqual(shared.errors,[]);
 assert.ok(shared.mediaBytes>=pic.length);assert.ok(shared.calls.every(c=>c.method==='GET'));assert.ok(shared.calls.some(c=>c.url.includes('channel_id='+C)&&c.url.includes('conversation_id=')));shared.dom.window.close();
 const before=await setup(true,true);assert.equal(before.mediaBytes,0);assert.equal(before.w.document.querySelectorAll('[data-chat-attachment]').length,0);
 const after=await setup(false,true),{w}=after;
 assert.equal(after.mediaBytes,pic.length);assert.equal(after.listCalls,before.listCalls);assert.equal(after.listBytes,before.listBytes);
  assert.equal(w.document.querySelectorAll('[data-chat-attachment]').length,1);
 assert.match(w.document.getElementById('chatRows').textContent,/Alice/);assert.match(w.document.getElementById('chatRows').textContent,/0:42/);assert.equal(w.document.querySelectorAll('[data-direction="outgoing"]').length,1);
 const details=w.document.querySelector('#chatRows details');const n=after.calls.length;details.open=true;await settle();assert.equal(after.calls.length,n);assert.match(details.textContent,/Salam! Привет!/);
  const buttons=[...w.document.querySelectorAll('[data-chat-attachment] button')];assert.equal(w.document.querySelectorAll('#chatRows img').length,1);assert.equal(buttons.length,1);assert.equal(buttons[0].hidden,true);
  const audio=w.document.querySelector('#chatRows audio');assert.ok(audio.controls,'voice uses visible native audio controls');assert.equal(audio.preload,'none');const audioQuery=new URL(audio.src).searchParams;assert.equal(audioQuery.get('message_id'),'connect:00000000-0000-0000-0000-000000000502');assert.equal(audioQuery.get('channel_id'),C);assert.equal(audioQuery.get('expected_tenant_id'),A);
  assert.equal(after.mediaBytes,pic.length);assert.equal(after.calls.filter(c=>c.url.includes('/attachment')).length,1);assert.ok(after.calls.every(c=>c.method==='GET'));
  const measured={before:{listRequests:before.listCalls,listJsonBytes:before.listBytes,openRequests:before.historyCalls,openJsonBytes:before.historyBytes,mediaBytes:before.mediaBytes},after:{listRequests:after.listCalls,listJsonBytes:after.listBytes,openRequests:after.historyCalls,openJsonBytes:after.historyBytes,mediaBytesOnOpen:pic.length,previewRequests:1,previewBytes:after.mediaBytes}};
  w.document.getElementById('closeModal').click();assert.equal(after.revoked.length,1);
 after.setUnavailable();await w.tenantOperator.openDialog(after.route);await settle();assert.match(w.document.querySelector('[data-chat-attachment] [role="status"]').textContent,/Файл недоступен/);
 w.tenantI18n.setLanguage('az');assert.equal(w.document.querySelector('[data-chat-attachment] button').textContent,'Şəkli aç');w.tenantI18n.setLanguage('en');assert.equal(w.document.querySelector('[data-chat-attachment] button').textContent,'View photo');
 w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{...after.profile.member,tenant_id:'other'},capabilities:{modules:{dialogs:false}}}}));assert.equal(w.document.querySelectorAll('#chatRows img,#chatRows audio').length,0);
 assert.deepEqual(after.errors,[]);console.log('PERFORMANCE '+JSON.stringify(measured));console.log('PASS: automatic photo preview, lazy audio, stored transcript, localization, group authors, no AI/send requests, stale identity cleanup.');before.dom.window.close();after.dom.window.close();
})().catch(e=>{console.error(e);process.exitCode=1;});
