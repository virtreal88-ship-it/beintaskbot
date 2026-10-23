const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.resolve(__dirname,'..'),membersScript=fs.readFileSync(path.join(root,'docs/tenant-members-ui.js'),'utf8'),workspaceScript=fs.readFileSync(path.join(root,'docs/tenant-workspace.js'),'utf8');
const tick=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r))};
function create(profile,allowed,pending=false){
 const dom=new JSDOM('<body><section id="view-deals"><div class="toolbar"></div></section><section id="view-settings"><div class="top"></div><div id="chakra-settings-root"></div><div class="grid"></div></section></body>',{url:'https://app.test/app?view=settings',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window;w.scrollTo=()=>{};w.tenantI18n={language:'ru'};w.confirm=()=>true;
 w.tenantProfileSnapshot={member:profile,capabilities:{can_manage_employees:allowed}};let release;
 w.fetch=async url=>({ok:true,json:async()=>url.endsWith('/me')?w.tenantProfileSnapshot:url.endsWith('/members')?(pending?new Promise(r=>release=r):{success:true,members:[{telegram_id:2,display_name:'Demo',role:'worker',active:true}]}):{success:true,requests:[]}});
 w.eval(workspaceScript);w.eval(membersScript);
 return {dom,w,release:()=>release({success:true,members:[{telegram_id:2,display_name:'Old tenant private name',role:'worker'}]})};
}
(async()=>{
 for(const [role,allowed] of [['owner',true],['admin',true],['admin',false],['manager',false],['operator',false]]){
  const {dom,w}=create({tenant_id:'a',telegram_id:1,role},allowed);await tick();const d=w.document;
  assert.equal(d.getElementById('staffManagement').hidden,!allowed,role);
  assert.equal(d.querySelector('[data-settings-tab=employees]').hidden,!allowed,role);
  if(allowed){const staff=d.getElementById('staffManagement');d.getElementById('chakra-settings-root').innerHTML='<div>React rerender</div>';await tick();assert.equal(staff.isConnected,true);assert.equal(d.querySelectorAll('#staffManagement').length,1);assert.equal(d.querySelectorAll('#staffMemberModal').length,1)}
  dom.window.close();
 }
 const f=create({tenant_id:'a',telegram_id:1,role:'owner'},true,true);await tick();
 f.w.document.dispatchEvent(new f.w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'b',telegram_id:1,role:'operator'},capabilities:{can_manage_employees:false}}}));
 f.release();await tick();
 assert.equal(f.w.document.getElementById('staffManagement').hidden,true);
 assert.ok(!f.w.document.getElementById('staffManagement').textContent.includes('Old tenant'));
 assert.equal(f.w.document.querySelector('[data-settings-tab=employees]').hidden,true);
 assert.equal(f.w.document.body.classList.contains('staff-modal-open'),false);
 f.dom.window.close();console.log('PASS: owner/admin authority, denied roles, React rerender, tenant switch and stale requests.');
})().catch(e=>{console.error(e);process.exitCode=1});
