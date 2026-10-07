const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
 constructor(){this.children=[];this.textContent='';this.disabled=false;}
 append(...items){this.children.push(...items);}replaceChildren(){this.children=[];}
 set innerHTML(value){this.parts={'.push-connect':new Element(),'.push-status':new Element(),'.push-devices':new Element()};}
 querySelector(key){return this.parts[key];}
}
const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-push.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function setup(){
 const grid=new Element(),listeners={},calls=[],state={permission:'granted',requests:0,unsubscribed:0,devices:[],postError:false};
 const profile={tenant_id:'company-a',telegram_id:20,role:'owner'};
 const sub={toJSON:()=>({endpoint:'https://fcm.googleapis.com/fcm/send/device',keys:{}}),unsubscribe:async()=>{state.unsubscribed++;}};
 const reg={scope:'https://crm.pro.az/app/',active:true,pushManager:{getSubscription:async()=>sub,subscribe:async()=>sub}};
 const window={isSecureContext:true,PushManager:class{},Notification:{}};
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
 vm.runInNewContext(source,{window,navigator,document,fetch,Notification:notification,Uint8Array,URL,atob:value=>Buffer.from(value,'base64').toString('binary'),setTimeout,clearTimeout});
 return{grid,listeners,calls,state,profile,window,reg,navigator};
}
(async()=>{
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
 const sw=fs.readFileSync(path.join(__dirname,'../docs/tenant-push-sw.js'),'utf8'),events={},notifications=[],opened=[];
 vm.runInNewContext(sw,{self:{addEventListener:(name,fn)=>events[name]=fn,registration:{showNotification:async(title,data)=>notifications.push([title,data])},clients:{openWindow:async(url)=>opened.push(url)}}});
 let work;events.push({data:{json:()=>({body:'PRIVATE TASK',url:'https://evil.test'})},waitUntil:promise=>work=promise});await work;
 assert(!JSON.stringify(notifications).includes('PRIVATE'));assert.equal(notifications[0][1].data.url,'/app');
 events.notificationclick({notification:{close:()=>{}},waitUntil:promise=>work=promise});await work;assert.deepEqual(opened,['/app']);
 console.log('PASS: opt-in push UI, denied permission, tenant mismatch, protected logout, generic SW and safe notification URL.');
})().catch(error=>{console.error(error);process.exitCode=1;});
