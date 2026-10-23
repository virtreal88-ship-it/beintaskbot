const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'..','docs/tenant-push-sw.js'),'utf8');
const handlers={},shown=[],opened=[];const self={location:{origin:'https://crm.test'},registration:{showNotification:(title,options)=>{shown.push({title,options});return Promise.resolve();}},clients:{openWindow:url=>{opened.push(url);return Promise.resolve();}},addEventListener:(name,handler)=>handlers[name]=handler};
vm.runInNewContext(source,{self,URL,Promise});
(async()=>{
 const target='/app?view=dialogs&open_dialog=1&provider=connect&lead_id=41&channel_id=00000000-0000-0000-0000-000000000201&conversation_id=994501234567%40s.whatsapp.net';
 let pushPromise;handlers.push({data:{json:()=>({kind:'crm_notice',event:'incoming_message',url:target,body:'private chat text'})},waitUntil:promise=>pushPromise=promise});await pushPromise;
 assert.equal(shown[0].title,'CRM Smart Assistant');assert.equal(shown[0].options.body,'Yeni mesaj var. Kabinetdə yoxlayın.');assert.match(shown[0].options.tag,/^crm-dialog-[0-9a-f]+$/);
 const otherTarget=target.replace('lead_id=41','lead_id=42');let secondPush;handlers.push({data:{json:()=>({kind:'crm_notice',event:'incoming_message',url:otherTarget})},waitUntil:promise=>secondPush=promise});await secondPush;assert.notEqual(shown[1].options.tag,shown[0].options.tag,'different dialogues keep separate visible notifications');assert.doesNotMatch(shown[0].options.body,/private chat text|994501234567|00000000/);assert.doesNotMatch(shown[0].title,/private chat text|994501234567|00000000/);
 let clickPromise;handlers.notificationclick({notification:{data:{url:target},close(){}},waitUntil:promise=>clickPromise=promise});await clickPromise;assert.equal(opened[0],target);
 let badPromise;handlers.notificationclick({notification:{data:{url:'https://evil.test/app?view=dialogs'} ,close(){}},waitUntil:promise=>badPromise=promise});await badPromise;assert.equal(opened[1],'/app');
 console.log('PASS: neutral Connect message Push, per-dialog notification tag, safe deep link click and unsafe-target fallback');
})().catch(error=>{console.error(error);process.exitCode=1;});