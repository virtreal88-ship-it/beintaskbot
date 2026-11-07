const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');

const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8');
const demand=fs.readFileSync(path.join(root,'docs/tenant-view-demand.js'),'utf8');
const source=fs.readFileSync(path.join(root,'ui/src/main.jsx'),'utf8');
const profile={member:{tenant_id:'tenant-a',telegram_id:17,role:'owner',active:true,permissions:[]},capabilities:{modules:{}}};
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};

(async()=>{
 assert.match(source,/host === "chakra-notification-settings-root" \? "#chakra-notification-settings-root"/,
   'the notification root itself is watched for edits');
 const dom=new JSDOM(html,{url:'https://crm.test/app?view=settings',runScripts:'outside-only'}),w=dom.window,d=w.document;
 let now=1_000_000,activations=0,resets=0;
 w.Date.now=()=>now;
 const view=d.getElementById('view-settings'),host=d.createElement('div'),field=d.createElement('input');
 view.classList.add('active');
 host.id='chakra-notification-settings-root';host.append(field);view.append(host);
 w.eval(demand);
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));
 w.tenantViewDemand.watch({view:'settings',host:host.id,editable:'#chakra-notification-settings-root',activate:()=>activations++,reset:()=>resets++});
 await settle();assert.equal(activations,1,'visible notification settings activate');
 field.value='unsaved preference';field.dispatchEvent(new w.Event('change',{bubbles:true}));
 host.hidden=true;await settle();now+=31_000;host.hidden=false;await settle();
 assert.equal(resets,0,'reopening after the retention TTL preserves an unsaved preference');
 assert.equal(activations,1,'the dirty component is not remounted');
 w.tenantViewDemand.clean('#chakra-notification-settings-root');
 host.hidden=true;await settle();now+=31_000;host.hidden=false;await settle();
 assert.equal(resets,1,'a clean component can still be refreshed after the retention TTL');
 assert.equal(activations,2);
 dom.window.close();
 console.log('PASS: notification preferences retain unsaved edits when reopened and refresh clean state after TTL.');
})().catch(error=>{console.error(error);process.exitCode=1;});
