const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.SUPPORT_CASES_JSDOM||'../.support-test-deps/node_modules/jsdom');
const script=fs.readFileSync('docs/tenant-knowledge-candidates.js','utf8'),tick=()=>new Promise(r=>setTimeout(r,30));
const id='22222222-2222-4222-8222-222222222222',hash='a'.repeat(64);
async function run(role,view){
 const dom=new JSDOM('<div id="supportCases"></div><span id="supportCasesPending"></span>',{url:'https://example.test/app?view='+view+'&support_case_id='+id,runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];
 const card={id,version:2,status:'knowledge_published',title:'Owner case',symptoms:['Issue'],resolution_steps:['Old'],owner_additions:'',cause:{status:'unknown',text:''},outcome:{status:'unconfirmed',text:'',message_ids:[]},source_message_ids:['m1'],knowledge_publication:{source_id:'s1',active:true,current:true,source_hash:hash}};
 w.tenantProfileSnapshot={member:{tenant_id:'tenant-a',telegram_id:7,role}};w.tenantI18n={language:'ru'};
 w.fetch=async(url,opt={})=>{calls.push(url);let result={};if(url.includes('/knowledge-candidates?'))return{ok:false,status:400,json:async()=>({success:false,error:'Некорректные параметры.'})};if(url.includes('/knowledge-candidates/'+id+'?'))result={candidate:card,evidence:[],route:{}};else if(url.includes('/ai/agents'))result={agents:[]};else if(url.includes('/ai/channels'))result={channels:[]};else if(url.includes('/ai/dialogs'))result={dialogs:[]};return{ok:true,status:200,json:async()=>({success:true,result})}};
 w.eval(script);await tick();await tick();
 const exact=calls.filter(x=>x.includes('/knowledge-candidates/'+id+'?'));
 if(role==='owner'&&view==='support_cases'){assert.equal(exact.length,1);assert(d.querySelector('[data-published-correction]'));assert(exact[0].includes('expected_tenant_id=tenant-a'));}else{assert.equal(exact.length,0);assert.equal(d.querySelector('[data-published-correction]'),null)}
 w.close();
}
(async()=>{await run('owner','support_cases');await run('manager','support_cases');await run('owner','dialogs');console.log('PASS direct owner case opens even if list fails; other views/roles do not request it')})().catch(e=>{console.error(e);process.exitCode=1});
