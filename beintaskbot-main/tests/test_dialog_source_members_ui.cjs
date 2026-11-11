const assert=require('node:assert/strict');const fs=require('node:fs');const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const script=fs.readFileSync(require('node:path').join(__dirname,'../docs/tenant-dialog-source-members.js'),'utf8');
const source={source_id:'00000000-0000-0000-0000-000000000201',name:'Support',active:true,employee_ids:[34],revision:0};
const tick=()=>new Promise(r=>setTimeout(r,0));
function env(){const dom=new JSDOM('<section id="mount"></section>',{url:'https://app.test',runScripts:'outside-only'});const w=dom.window;w.tenantI18n={language:'en'};w.eval(script);return {dom,w,host:w.document.querySelector('section')}}
function open(e,isCurrent=()=>true){e.w.document.dispatchEvent(new e.w.CustomEvent('tenant-member-card-open',{detail:{member:{telegram_id:33},profile:{role:'owner',tenant_id:'a',telegram_id:11},container:e.host,isCurrent}}))}
(async()=>{
 const e=env();let result={sources:[source],preflight:{ready:true}},posts=[],resolvePost;
 e.w.fetch=async(url,opt)=>{if(opt.method==='POST'){posts.push(JSON.parse(opt.body));await new Promise(r=>resolvePost=r);result={sources:[{...source,employee_ids:[33,34],revision:1}],preflight:{ready:true}};}return {ok:true,json:async()=>({success:true,result})}};
 open(e);await tick();e.host.querySelector('input').checked=true;e.host.querySelector('button').click();e.host.querySelector('button').click();await tick();assert.equal(posts.length,1,'repeated click is single flight');assert.deepEqual(posts[0].employee_ids,[34,33]);assert.equal(posts[0].expected_revision,0);assert.equal(posts[0].expected_tenant_id,'a');assert.equal(posts[0].expected_user_id,'11');resolvePost();await tick();await tick();assert.match(e.host.textContent,/assignments saved/);e.dom.window.close();
 const stale=env();let finish,current=true;stale.w.fetch=()=>new Promise(r=>finish=r);open(stale,()=>current);current=false;stale.host.textContent='new company';finish({ok:true,json:async()=>({success:true,result})});await tick();assert.equal(stale.host.textContent,'new company','late response cannot populate a changed card');stale.dom.window.close();
 const denied=env();denied.w.fetch=async()=>({ok:false,json:async()=>({success:false,error:'Source assignments changed. Refresh.'})});open(denied);await tick();assert.match(denied.host.textContent,/Refresh/);assert.equal(denied.host.querySelector('button').disabled,true);denied.dom.window.close();
 console.log('PASS: source assignments retain other employees, bind identity/version, prevent repeat sends and discard stale card responses.');
})().catch(e=>{console.error(e);process.exitCode=1});
