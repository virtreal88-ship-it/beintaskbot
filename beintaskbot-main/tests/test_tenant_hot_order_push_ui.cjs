const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const events={},notices=[],opened=[];let pending;
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../docs/tenant-push-sw.js'),'utf8'),{
  self:{addEventListener:(name,callback)=>events[name]=callback,registration:{showNotification:async(title,data)=>notices.push(data)},clients:{openWindow:async url=>opened.push(url)}}
});
(async()=>{
  events.push({data:{json:()=>({kind:'hot_order',body:'PRIVATE CLIENT',url:'https://evil.invalid'})},waitUntil:p=>pending=p});await pending;
  assert.equal(notices[0].data.url,'/app?view=hot_orders');assert.ok(!JSON.stringify(notices).includes('PRIVATE'));
  events.notificationclick({notification:{data:notices[0].data,close(){}},waitUntil:p=>pending=p});await pending;
  assert.equal(opened[0],'/app?view=hot_orders');
  events.notificationclick({notification:{data:{url:'https://evil.invalid'},close(){}},waitUntil:p=>pending=p});await pending;
  assert.equal(opened[1],'/app');
  console.log('PASS: hot-order push uses generic text and only allowlisted cabinet links.');
})().catch(error=>{console.error(error);process.exitCode=1});
