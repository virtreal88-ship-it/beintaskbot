const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
 constructor(){this.children=[];this.textContent='';this.disabled=false;}
 append(...items){this.children.push(...items);}replaceChildren(){this.children=[];}
 set innerHTML(value){this.parts={'.push-connect':new Element(),'.push-status':new Element(),'.push-devices':new Element()};}
 querySelector(key){return this.parts[key];}
}
const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-push.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function setup(deferDigest=false){
 const grid=new Element(),listeners={},calls=[],digestResolvers=[],state={permission:'granted',requests:0,subscribeCalls:0,unsubscribed:0,devices:[],postError:false,deferDigest,noticeUpdates:0};
 const profile={tenant_id:'company-a',telegram_id:20,role:'owner'};
 const sub={endpoint:'https://fcm.googleapis.com/fcm/send/device',toJSON:()=>({endpoint:'https://fcm.googleapis.com/fcm/send/device',keys:{}}),unsubscribe:async()=>{state.unsubscribed++;}};
 const reg={scope:'https://crm.pro.az/app/',active:true,pushManager:{getSubscription:async()=>sub,subscribe:async()=>{state.subscribeCalls++;return sub;}}};
 const window={isSecureContext:true,PushManager:class{},Notification:{},tenantOperatorUpdateBrowserNotice(){state.noticeUpdates++;},crypto:{subtle:{digest:async()=>state.deferDigest?await new Promise(resolve=>digestResolvers.push(resolve)):new Uint8Array(32).fill(0xaa).buffer}}};
 const navigator={platform:'Phone',serviceWorker:{register:async(url,opt)=>{calls.push(['register',url,opt]);return reg;},getRegistration:async()=>reg}};
 const document={createElement:()=>new Element(),querySelector:()=>grid,addEventListener:(name,fn)=>{listeners[name]=fn;}};
 const fetch=async(url,options={})=>{
   calls.push([url,options]);
   if(url==='/api/platform/me')return{json:async()=>({success:true,member:profile})};
   if(options.method==='POST'){
     const body=JSON.parse(options.body);if(body.action==='subscribe')state.devices=[{device_id:'a'.repeat(64),label:'Phone'}];else state.devices=[];
     return{ok:!state.postError,json:async()=>state.postError?{success:false,error:'API failed'}:{success:true}};
   }
   return{ok:true,json:async()=>({success:true,devices:state.devices,public_key:Buffer.alloc(65).toString('base64url'),delivery_enabled:true,tenant_id:profile.tenant_id,user_id:String(profile.telegram_id)})};
 };
 const notification={requestPermission:async()=>{state.requests++;return state.permission;}};
 vm.runInNewContext(source,{window,navigator,document,fetch,Notification:notification,Uint8Array,URL,TextEncoder,atob:value=>Buffer.from(value,'base64').toString('binary'),setTimeout,clearTimeout});
 return{grid,listeners,calls,state,profile,window,reg,navigator,digestResolvers};
}
(async()=>{
 const reloaded=setup(true);reloaded.state.devices=[{device_id:'aa'.repeat(32),label:'Current device'}];await tick();assert.equal(reloaded.window.tenantPushConnectionState,'loading','device state remains unknown while the saved browser subscription is checked');assert.equal(reloaded.state.requests,0,'reload does not ask for browser permission');assert.equal(reloaded.state.subscribeCalls,0,'reload does not create or renew a push subscription');assert.equal(reloaded.digestResolvers.length,1,'the existing browser subscription is matched against the server device list');reloaded.digestResolvers[0](new Uint8Array(32).fill(0xaa).buffer);await tick();assert.equal(reloaded.window.tenantPushConnectionState,'connected','the saved current-device subscription is restored after matching');assert.equal(reloaded.window.tenantPushConnected,true);
 const app=setup();await tick();const panel=app.grid.children[0],button=panel.querySelector('.push-connect');
 assert.equal(app.state.requests,0,'No automatic permission prompt');
 await button.onclick();assert.equal(app.state.requests,1);
 const post=app.calls.find(([url,opt])=>url==='/api/platform/push/devices'&&opt.method==='POST');
 assert.equal(JSON.parse(post[1].body).expected_tenant_id,'company-a');assert.equal(JSON.parse(post[1].body).expected_user_id,'20');
 assert.equal(app.calls.find(call=>call[0]==='register')[2].scope,'/app/');
 assert(panel.querySelector('.push-status').textContent.includes('Cihaz qoşuldu'));
 assert.equal(panel.querySelector('.push-devices').children.length,1);
 await app.window.tenantPushLogout();assert.equal(app.state.unsubscribed,1);
 app.reg.scope='https://crm.pro.az/';await app.window.tenantPushLogout();assert.equal(app.state.unsubscribed,1,'Legacy worker must remain untouched');
 const denied=setup();await tick();denied.state.permission='denied';await denied.grid.children[0].querySelector('.push-connect').onclick();
 assert(!denied.calls.some(([,opt])=>opt?.method==='POST'));
 assert(denied.grid.children[0].querySelector('.push-status').textContent.includes('icazəsi'));
 const changed=setup();await tick();let resolvePermission;
 // A new profile invalidates callbacks and prevents sending old identity.
 changed.listeners['tenant-profile']({detail:{member:{tenant_id:'company-b',telegram_id:99,role:'owner'}}});
 await tick();assert(changed.grid.children[0].querySelector('.push-status').textContent.includes('Kabinet dəyişib'));
 const stale=setup();stale.state.devices=[{device_id:'aa'.repeat(32),label:'Current device'}];await tick();await tick();assert.equal(stale.window.tenantPushConnected,true,'a matching current browser device is recognized');stale.state.deferDigest=true;const setupPending=stale.window.tenantPushEnable();for(let i=0;i<10&&stale.digestResolvers.length===0;i++)await tick();assert.equal(stale.digestResolvers.length,1,'a subscription refresh can be pending after its list response');const remove=stale.grid.children[0].querySelector('.push-devices').children[0].children[1];const removePending=remove.onclick();for(let i=0;i<10&&stale.digestResolvers.length<2;i++)await tick();assert.equal(stale.digestResolvers.length,2,'unsubscribe starts a newer device-state refresh');const noticesBefore=stale.state.noticeUpdates;stale.digestResolvers[0](new Uint8Array(32).fill(0xaa).buffer);await tick();assert.equal(stale.state.noticeUpdates,noticesBefore,'the late pre-unsubscribe response is ignored');stale.digestResolvers[1](new Uint8Array(32).fill(0xaa).buffer);await Promise.all([setupPending,removePending]);assert.equal(stale.window.tenantPushConnected,false,'unsubscribe refresh wins and clears this browser device state');
 const staleProfile=setup(true);staleProfile.state.devices=[{device_id:'aa'.repeat(32),label:'Current device'}];for(let i=0;i<10&&staleProfile.digestResolvers.length===0;i++)await tick();assert.equal(staleProfile.digestResolvers.length,1,'device matching can remain pending after its tenant list response');staleProfile.listeners['tenant-profile']({detail:{member:{tenant_id:'company-b',telegram_id:99,role:'owner'}}});await tick();staleProfile.digestResolvers[0](new Uint8Array(32).fill(0xaa).buffer);await tick();assert.equal(staleProfile.window.tenantPushConnected,false,'a late device match from the previous tenant cannot restore connected status');
 const sw=fs.readFileSync(path.join(__dirname,'../docs/tenant-push-sw.js'),'utf8'),events={},notifications=[],opened=[];
 vm.runInNewContext(sw,{self:{addEventListener:(name,fn)=>events[name]=fn,registration:{showNotification:async(title,data)=>notifications.push([title,data])},clients:{openWindow:async(url)=>opened.push(url)}}});
 let work;events.push({data:{json:()=>({body:'PRIVATE TASK',url:'https://evil.test'})},waitUntil:promise=>work=promise});await work;
 assert(!JSON.stringify(notifications).includes('PRIVATE'));assert.equal(notifications[0][1].data.url,'/app');
 events.notificationclick({notification:{close:()=>{}},waitUntil:promise=>work=promise});await work;assert.deepEqual(opened,['/app']);
 console.log('PASS: opt-in push UI, denied permission, tenant mismatch, protected logout, generic SW and safe notification URL.');
})().catch(error=>{console.error(error);process.exitCode=1;});
