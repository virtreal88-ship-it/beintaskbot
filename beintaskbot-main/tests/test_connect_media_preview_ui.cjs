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
async function setup(previous=false,group=false,shared=false,initialUnavailable=false){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app/dialogs?site_lang=ru',runScripts:'outside-only'}),w=dom.window,calls=[],revoked=[],errors=[];
 w.HTMLAnchorElement.prototype.click=function(){};w.HTMLMediaElement.prototype.load=function(){};w.HTMLMediaElement.prototype.pause=function(){Object.defineProperty(this,'paused',{value:true,configurable:true});this.dispatchEvent(new w.Event('pause'));};w.HTMLMediaElement.prototype.play=async function(){Object.defineProperty(this,'paused',{value:false,configurable:true});this.dispatchEvent(new w.Event('play'));};
 w.addEventListener('error',e=>errors.push(e.error));w.Blob=Blob;w.AbortController=AbortController;w.URL.createObjectURL=()=> 'blob:test/'+Math.random();w.URL.revokeObjectURL=v=>revoked.push(v);
 const member={tenant_id:A,telegram_id:33,role:'worker',permissions:['dialogs'],active:true};
 const profile={member,capabilities:{modules:{dialogs:true}}};w.tenantProfileSnapshot=profile;
 const route={provider:'connect',lead_id:41,channel_id:C,conversation_id:group?'group@g.us':'direct@s.whatsapp.net',customer_kind:group?'group':'contact',customer_name:group?'Test group':'Test customer',avatar_status:'',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:false};
 const messages=[{id:'row-image',external_id:'connect:00000000-0000-0000-0000-000000000501',source:'connect',direction:'incoming',message_type:'image',body:'Photo',author_type:group?'participant':'customer',author_name:'Alice'},
 {id:'row-audio',external_id:'connect:00000000-0000-0000-0000-000000000502',source:'connect',direction:'outgoing',message_type:'audio',body:'Salam! Привет!',duration_seconds:42,...(previous?{}:{has_transcription:true}),author_type:'operator',author_name:'Operator'},
 {id:'row-video',external_id:'connect:00000000-0000-0000-0000-000000000503',source:'connect',direction:'incoming',message_type:'video',body:'',author_type:'customer'},
 {id:'row-file',external_id:'connect:00000000-0000-0000-0000-000000000504',source:'connect',direction:'incoming',message_type:'document',body:'',author_type:'customer'}];
 let unavailable=initialUnavailable,mediaBytes=0,jsonBytes=0;
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
 if(!previous)assert.ok(w.document.querySelector('#operatorRows .operator-row-avatar-image'),'visible list rows can request a missing avatar with bounded lazy hydration');
 await w.tenantOperator.openDialog(route);
 const avatar=w.document.querySelector('#operatorConversationHeader .operator-header-avatar');if(!previous){assert.ok(avatar,'opening the selected dialog starts the one-time avatar request');assert.ok(avatar.querySelector('img')?.getAttribute('src')?.includes('dialogs/avatar')||avatar.querySelector('img')?.getAttribute('data-avatar-src')?.includes('dialogs/avatar'));assert.ok(avatar.querySelector('img')?.getAttribute('src')?.includes(C)||avatar.querySelector('img')?.getAttribute('data-avatar-src')?.includes(C));}await settle();
 return {dom,w,calls,revoked,errors,route,profile,listCalls,listBytes,historyCalls:calls.length-listCalls,historyBytes:jsonBytes-listBytes,get mediaBytes(){return mediaBytes},setUnavailable:()=>unavailable=true};
}
(async()=>{
 const shared=await setup(false,true,true);assert.deepEqual(shared.errors,[]);assert.equal(shared.mediaBytes,0,'opening the dialog does not fetch attachments');shared.w.document.querySelector('[data-chat-attachment][data-media-kind=image] [data-media-menu-load]').click();await settle();
 await shared.w.document.getElementById('operatorEarlier').onclick();await settle();assert.deepEqual(shared.errors,[]);
 assert.ok(shared.mediaBytes>=pic.length);assert.ok(shared.calls.every(c=>c.method==='GET'));assert.ok(shared.calls.some(c=>c.url.includes('channel_id='+C)&&c.url.includes('conversation_id=')));shared.dom.window.close();
 const before=await setup(true,true);assert.equal(before.mediaBytes,0);assert.equal(before.w.document.querySelectorAll('[data-chat-attachment]').length,0);
 const after=await setup(false,true),{w}=after;
 assert.equal(after.mediaBytes,0,'no preview/download request is made while opening a dialog');assert.ok(after.listCalls<before.listCalls,'startup profile/list triggers now coalesce');assert.ok(after.listBytes<before.listBytes,'deduplication avoids a repeated list payload');
  assert.equal(w.document.querySelectorAll('[data-chat-attachment]').length,4,'photo, voice, video and document use scoped explicit attachment actions');assert.equal(w.document.querySelectorAll('[data-chat-attachment] [data-media-menu-load]').length,4);assert.equal(w.document.querySelectorAll('[data-chat-attachment] [data-media-download]').length,4);
 assert.match(w.document.getElementById('chatRows').textContent,/Alice/);assert.match(w.document.getElementById('chatRows').textContent,/0:42/);assert.equal(w.document.querySelectorAll('[data-direction="outgoing"]').length,1);
 const transcript=w.document.querySelector('#chatRows [data-audio-transcript]');const n=after.calls.length;transcript.open=true;await settle();assert.equal(after.calls.length,n);assert.match(transcript.textContent,/Salam! Привет!/);
  const photoHost=w.document.querySelector('[data-chat-attachment][data-media-kind=image]');const photoButtons=[...photoHost.querySelectorAll('button')];assert.equal(w.document.querySelectorAll('#chatRows img').length,0,'photo is not fetched before user action');assert.equal(photoButtons.find(button=>button.matches('[data-media-open]')).hidden,false);assert.equal(photoHost.querySelectorAll('[data-media-menu-load],[data-media-download]').length,2);assert.equal(photoHost.querySelector('[data-media-menu-load]').hidden,false);photoHost.querySelector('[data-media-menu-load]').click();await settle();assert.equal(w.document.querySelectorAll('#chatRows img').length,1);assert.equal(photoHost.querySelector('[data-media-open]').hidden,true);assert.equal(photoHost.querySelector('[data-media-menu-load]').hidden,true);
  const audio=w.document.querySelector('#chatRows audio');assert.ok(audio.controls,'voice uses visible native audio controls');assert.equal(audio.preload,'none');assert.equal(audio.hasAttribute('src'),false,'voice URL is not eagerly fetched before explicit load');const audioHost=audio.closest('[data-chat-attachment]');assert.equal(audioHost.querySelectorAll('[data-media-menu-load],[data-media-download]').length,2);const audioLoad=audioHost.querySelector('[data-media-menu-load]');audioLoad.click();await settle();assert.ok(audio.src.startsWith('blob:test/'),'explicit load restores playable native audio controls');const audioRequest=after.calls.find(c=>c.url.includes('000502'));assert.ok(audioRequest);const audioQuery=new URL(audioRequest.url,'https://test.invalid').searchParams;assert.equal(audioQuery.get('message_id'),'connect:00000000-0000-0000-0000-000000000502');assert.equal(audioQuery.get('channel_id'),C);assert.equal(audioQuery.get('conversation_id'),after.route.conversation_id);assert.equal(audioQuery.get('expected_tenant_id'),A);
  assert.equal(after.mediaBytes,pic.length+sound.length);assert.equal(after.calls.filter(c=>c.url.includes('/attachment')).length,2);await w.tenantDownloadAttachment(audioHost.querySelector('[data-media-download]'),audioHost.querySelector('[data-media-download]').dataset.mediaDownload,audioHost.querySelector('[role="status"]'));assert.ok(after.calls.some(c=>new URL(c.url,'https://test.invalid').searchParams.get('download')==='1'),'download uses explicit download=1 endpoint');assert.equal(after.calls.filter(c=>c.url.includes('/attachment')).length,3);assert.ok(after.calls.every(c=>c.method==='GET'));
  const measured={before:{listRequests:before.listCalls,listJsonBytes:before.listBytes,openRequests:before.historyCalls,openJsonBytes:before.historyBytes,mediaBytes:before.mediaBytes},after:{listRequests:after.listCalls,listJsonBytes:after.listBytes,openRequests:after.historyCalls,openJsonBytes:after.historyBytes,mediaBytesAfterExplicitLoad:pic.length+sound.length,previewRequests:2,previewBytes:after.mediaBytes}};
 w.document.hidden=true;w.document.dispatchEvent(new w.Event('visibilitychange'));assert.equal(audio.isConnected,true,'clearing a media preview keeps the native voice controls');assert.equal(audio.hasAttribute('src'),false,'clearing revokes the loaded voice URL');assert.equal(audioHost.querySelector('[data-media-menu-load]').hidden,false,'the menu can explicitly load it again');w.document.hidden=false;
  w.document.getElementById('closeModal').click();assert.equal(after.revoked.length,2);
 const unavailable=await setup(false,true,false,true);const uw=unavailable.w;uw.document.querySelector('[data-chat-attachment][data-media-kind=image] [data-media-menu-load]').click();await settle();assert.match(uw.document.querySelector('[data-chat-attachment][data-media-kind=image] [role="status"]').textContent,/Файл недоступен/);
 uw.tenantI18n.setLanguage('az');assert.equal(uw.document.querySelector('[data-chat-attachment][data-media-kind=image] button[data-media-open]').textContent,'Şəkli aç');uw.tenantI18n.setLanguage('en');assert.equal(uw.document.querySelector('[data-chat-attachment][data-media-kind=image] button[data-media-open]').textContent,'View photo');
 w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{...after.profile.member,tenant_id:'other'},capabilities:{modules:{dialogs:false}}}}));assert.equal(w.document.querySelectorAll('#chatRows img,#chatRows audio').length,0);
 assert.deepEqual(after.errors,[]);console.log('PERFORMANCE '+JSON.stringify(measured));console.log('PASS: explicit photo/audio load, lazy media, stored transcript, localization, group authors, no AI/send requests, stale identity cleanup.');await settle();before.dom.window.close();after.dom.window.close();await settle();unavailable.dom.window.close();
})().catch(e=>{console.error(e);process.exitCode=1;});
