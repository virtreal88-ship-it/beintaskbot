const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const events={},notices=[],opened=[];let pending;
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../docs/tenant-push-sw.js'),'utf8'),{
  self:{addEventListener:(name,callback)=>events[name]=callback,registration:{showNotification:async(title,data)=>notices.push(data)},clients:{openWindow:async url=>opened.push(url)}}
});
(async()=>{
  events.push({data:{json:()=>({kind:'linear',body:'PRIVATE CLIENT',url:'https://evil.invalid'})},waitUntil:p=>pending=p});await pending;
  assert.equal(notices[0].data.url,'/app?view=linear');assert.equal(notices[0].tag,'crm-linear');
  assert(!JSON.stringify(notices).includes('PRIVATE'));
  events.notificationclick({notification:{data:notices[0].data,close(){}},waitUntil:p=>pending=p});await pending;
  assert.equal(opened[0],'/app?view=linear');
  events.notificationclick({notification:{data:{url:'https://evil.invalid'},close(){}},waitUntil:p=>pending=p});await pending;
  assert.equal(opened[1],'/app');
  const rules=fs.readFileSync(path.join(__dirname,'../docs/tenant-linear-rules.js'),'utf8');
  assert.match(rules,/sync_enabled:/);assert.match(rules,/notification_state_ids:/);
  console.log('PASS: Linear notifications use generic text, allowlisted cabinet link and opt-in settings.');
})().catch(error=>{console.error(error);process.exitCode=1});
