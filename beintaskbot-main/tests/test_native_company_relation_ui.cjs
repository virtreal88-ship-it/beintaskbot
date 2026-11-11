/* Isolated renderers exercise real native card markup and click guards. */
const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const html=fs.readFileSync('docs/tenant-app.html','utf8');
const fragment=(start,end)=>html.slice(html.indexOf(start),html.indexOf(end,html.indexOf(start)));
const helpers=fragment('  const nativeAction=','  const navigationRoute=');
const deal=fragment('  async function renderNativeDealCard(', '  async function openNativeEntityCard(');
const company=fragment('  async function renderNativeCompanyCard(', "  document.addEventListener('click',e=>{const edit=");
const tick=()=>new Promise(r=>setTimeout(r,5));
async function scenario(updateCustomers,updateDeals,del){
 const dom=new JSDOM('<div id="nativeEntityBody"></div><input id="companySearch">',{runScripts:'outside-only'}),w=dom.window;
 w.eval(`var state={capabilities:{actions:{customers:{view:true,update:${updateCustomers},delete:${del}},deals:{view:true,update:${updateDeals}}}},me:{role:'manager'}};
 var writes=[],nativeCardCompanyId='company-1';
 var $=id=>document.getElementById(id),esc=value=>String(value||''),nativeDealLabels=()=>({pipeline:'P',stage:'S'});
 var api=async(path,opts)=>{if(opts){writes.push(JSON.parse(opts.body));return {}};return {companies:[{id:'company-2',name:'Company 2'}],data:[{id:'contact-2',display_name:'Contact 2'}],deals:[{id:'deal-2',title:'Deal 2'}],company:{id:'company-1'}}};
 var notice=()=>{},confirm=()=>true,openNativeEntityCard=async()=>{},loadNativeDeals=async()=>{},loadNativeCompanies=async()=>{};
 ${helpers}${deal}${company}`);
 await w.eval(`renderNativeDealCard({id:'deal-1',company_id:'company-1',contact_id:'contact-1'},[])`);
 const q=s=>w.document.querySelector(s);
 assert.equal(q('[data-deal-link-company]').hidden,!(updateCustomers&&updateDeals));
 assert.equal(q('[data-deal-company-select]').hidden,!(updateCustomers&&updateDeals));
 assert.equal(q('[data-deal-unlink-company]').hidden,!del);
 q('[data-deal-company-select]').value='company-2';q('[data-deal-link-company]').click();await tick();
 assert.equal(w.writes.length,updateCustomers&&updateDeals?1:0,'hidden link click is guarded');
 w.writes.length=0;
 await w.eval(`renderNativeCompanyCard({id:'company-1',contacts:[{id:'contact-1'}],deals:[{id:'deal-1'}]})`);
 for(const kind of ['contact','deal']){
  const button=q(`[data-company-link="${kind}"]`),allowed=updateCustomers&&(kind==='contact'||updateDeals);
  assert.equal(button.closest('.crm-relation-add').hidden,!allowed);
  assert.equal(q(`[data-company-unlink="${kind}"]`).hidden,!del);
  q(kind==='contact'?'#companyCardContactSelect':'#companyCardDealSelect').value=kind+'-2';
  if(!allowed){button.click();await tick();assert.equal(w.writes.length,0,'hidden company link is guarded');}
 }
 if(!del){q('[data-company-unlink]').click();await tick();assert.equal(w.writes.length,0,'hidden unlink is guarded');}
 w.close();
}
async function scopedScenario(allow,{denyRead=false,stale=false}={}){
 const dom=new JSDOM('<div id="nativeEntityBody"></div><input id="companySearch">',{runScripts:'outside-only'}),w=dom.window;
 const row={id:'company-1',name:'Company visible',contacts:denyRead?[]:[{id:'contact-1',display_name:'Existing'}],deals:[],actions:{update:allow,archive:false},relation_actions:{link_contact:allow,unlink_contact:allow}};
 w.eval(`var state={capabilities:{actions:{companies:{view:true,update:true,delete:false},customers:{view:${!denyRead},update:false,delete:false},deals:{view:false,update:false}}},me:{role:'manager'}};
 var writes=[],reads=[],nativeCardCompanyId='company-1',snapshot=${JSON.stringify(row)};
 var $=id=>document.getElementById(id),esc=value=>String(value||'');
 var api=async(path,opts)=>{if(opts){writes.push({path,body:JSON.parse(opts.body)});return {data:{id:'contact-2'}}};reads.push(path);if(path.endsWith('/contacts'))return {data:[{id:'contact-2',display_name:'Available'}]};return {company:snapshot}};
 var notice=()=>{},confirm=()=>true,loadNativeCompanies=async()=>{};
 ${helpers}${company}`);
 await w.eval('renderNativeCompanyCard(snapshot)');
 const q=s=>w.document.querySelector(s),link=q('[data-company-link="contact"]');
 assert.equal(link.closest('.crm-relation-add').hidden,!allow,'record relation authority overrides global controls');
 if(denyRead){assert(q('[data-company-contacts-unavailable]'));assert.equal(q('#companyCardContactSelect').options.length,1);assert.equal(w.reads.some(p=>p.endsWith('/contacts')),false,'company U never broadens contact R');w.close();return}
 assert.equal(q('[data-company-unlink="contact"]').hidden,!allow);
 q('#companyCardContactSelect').value='contact-2';
 if(stale)w.nativeCardCompanyId='company-2';
 link.click();await tick();
 assert.equal(w.writes.length,allow&&!stale?1:0);
 if(allow&&!stale){assert.equal(w.writes[0].path,'/api/platform/crm/native/companies/company-1');assert.deepEqual(JSON.parse(JSON.stringify(w.writes[0].body)),{action:'link_contact',entity_id:'contact-2'});q('[data-company-unlink="contact"]').click();await tick();assert.equal(w.writes.length,2);assert.equal(w.writes[1].body.action,'unlink_contact')}
 else{q('[data-company-unlink="contact"]').click();await tick();assert.equal(w.writes.length,0)}
 w.close();
}
(async()=>{for(const args of [[false,false,false],[true,false,false],[false,true,false],[true,true,false],[false,false,true]])await scenario(...args);await scopedScenario(true);await scopedScenario(false);await scopedScenario(true,{denyRead:true});await scopedScenario(true,{stale:true});console.log('PASS native company/deal relationship visibility and click guards');})().catch(e=>{console.error(e);process.exitCode=1});
