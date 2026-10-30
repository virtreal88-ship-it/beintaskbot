const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.SUPPORT_CASES_JSDOM||'../.support-test-deps/node_modules/jsdom');
const script=fs.readFileSync('docs/tenant-knowledge-candidates.js','utf8');
const tick=()=>new Promise(resolve=>setTimeout(resolve,20));
async function run(role,current){
 const dom=new JSDOM('<div id="supportCases"></div><span id="supportCasesPending"></span>',{url:'https://example.test/app?site_lang=ru',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];
 let card={id:'case-1',version:2,status:'knowledge_published',title:'Old',symptoms:['Issue'],resolution_steps:['Old step'],owner_additions:'Notes',cause:{status:'unknown',text:''},outcome:{status:'unconfirmed',text:'',message_ids:[]},source_message_ids:['m1'],publications:[{agent_id:'agent-1',source_id:'source-1'}],knowledge_publication:{source_id:'source-1',title:'Old',source_hash:'a'.repeat(64),active:true,current}};
 w.tenantProfileSnapshot={member:{tenant_id:'tenant-a',telegram_id:7,role}};w.tenantI18n={language:'ru'};w.confirm=()=>true;
 w.fetch=async(url,opt={})=>{calls.push({url,opt});let result={};
  if(url.endsWith('/correct-published')){const body=JSON.parse(opt.body);assert.equal(body.expected_version,2);assert.equal(body.expected_source_hash,'a'.repeat(64));assert.equal(body.expected_tenant_id,'tenant-a');assert.equal(String(body.expected_user_id),'7');assert.equal(body.title,'Corrected');assert.deepEqual(body.resolution_steps,['Record the bug']);assert(!('agent_ids' in body));assert(!('cause' in body));assert(!('outcome' in body));card={...card,title:body.title,resolution_steps:body.resolution_steps,version:3,knowledge_publication:{...card.knowledge_publication,title:body.title,source_hash:'b'.repeat(64)}};result={candidate:card,evidence:[],route:{}};
  }else if(url.includes('/ai/agents'))result={agents:[]};else if(url.includes('/ai/channels'))result={channels:[]};else if(url.includes('/ai/dialogs'))result={dialogs:[]};else if(url.includes('/knowledge-candidates/case-1?'))result={candidate:card,evidence:[],route:{}};else if(url.includes('/knowledge-candidates?'))result={candidates:[card]};
  return {ok:true,status:200,json:async()=>({success:true,result})};};
 w.eval(script);await tick();d.querySelector('[data-case-open="case-1"]').click();await tick();
 const panel=d.querySelector('[data-published-correction]');if(role!=='owner'||!current){assert.equal(panel,null);w.close();return;}
 assert(panel);panel.querySelector('[data-correction-title]').value='Corrected';panel.querySelector('[data-correction-steps]').value='Record the bug';panel.querySelector('[data-correction-save]').click();await tick();await tick();
 assert.equal(calls.filter(x=>x.url.endsWith('/correct-published')).length,1);assert(d.querySelector('#caseDetailNotice').textContent.includes('сохранено'),d.querySelector('#caseDetailNotice').textContent);assert.equal(d.querySelector('[data-correction-title]').value,'Corrected');assert(!calls.some(x=>x.url.endsWith('/publish-guidance')));w.close();
}
(async()=>{await run('owner',true);await run('admin',true);await run('owner',false);console.log('PASS published correction owner UI, current source guard, exact fields and no publication');})().catch(e=>{console.error(e);process.exitCode=1});
