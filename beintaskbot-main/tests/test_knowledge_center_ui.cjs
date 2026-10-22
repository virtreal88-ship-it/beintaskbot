const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const html=`<!doctype html><html><body><nav><button id="knowledgeCenterNav" data-view="knowledge_center"><span data-knowledge-center-label></span></button><button data-view="ai_agents"></button><button data-view="support_cases"></button></nav><main><section id="knowledgeCenter"></section></main></body></html>`;
const dom=new JSDOM(html,{url:'https://app.test/app?view=knowledge_center&site_lang=ru&agent_id=agent-1',runScripts:'dangerously',pretendToBeVisual:true});
const w=dom.window,A='tenant-a',U=11,INSTRUCTION='lesson-1',CASE='case-1',AGENT='agent-1';
w.tenantI18n={language:'ru'};w.tenantProfileSnapshot={member:{tenant_id:A,telegram_id:U},capabilities:{modules:{ai_agents:true}}};w.sessionStorage.setItem('tenant.app.view','knowledge_center');
const data={
 '/api/platform/ai/knowledge':[
  {id:'source-i',tenant_id:A,agent_id:AGENT,agent_name:'AI продавец',title:'Продажа',kind:'text',active:true,origin_type:'instruction',origin_id:INSTRUCTION,updated_at:'2026-10-22T10:00:00Z'},
  {id:'source-general',tenant_id:A,agent_id:null,agent_name:null,title:'Продажа',kind:'pdf',active:true,origin_type:'knowledge_source',origin_id:null,updated_at:'2026-10-22T09:00:00Z'},
  {id:'source-other-agent',tenant_id:A,agent_id:'agent-2',agent_name:'Other agent',title:'Private agent note',kind:'text',active:true,origin_type:'knowledge_source',origin_id:null,updated_at:'2026-10-22T07:00:00Z'}
 ],
 '/api/platform/instructions':{items:[{id:INSTRUCTION,title:'Продажа',state:'published',language:'az',updated_at:'2026-10-22T10:00:00Z'}],next_offset:null},
 '/api/platform/ai/knowledge-candidates':{candidates:[{id:CASE,title:'Неправильный размер',problem:'Клиент получил другой размер.',status:'pending_review',updated_at:'2026-10-22T08:00:00Z'}],next_before:null},
 '/api/platform/ai/agents':[{id:AGENT,name:'AI продавец'}]
};
w.fetch=async input=>{const url=new URL(input,w.location.href);const key=url.pathname;assert.equal(url.searchParams.get('expected_tenant_id'),A);assert.equal(url.searchParams.get('expected_user_id'),String(U));const result=data[key];if(result===undefined)throw Error(`Unexpected request ${key}`);return {ok:true,status:200,json:async()=>({success:true,result})};};
w.eval(fs.readFileSync(path.join(__dirname,'../docs/tenant-knowledge-center.js'),'utf8'));
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
(async()=>{for(let i=0;i<20&&!w.document.querySelectorAll('.kc-card').length;i++)await tick();
 const cards=[...w.document.querySelectorAll('.kc-card')];assert.equal(cards.length,3);assert.ok(!w.document.querySelector('#kcList').textContent.includes('Private agent note'),'agent filter excludes other agents while retaining shared knowledge');assert.match(cards[0].textContent,/Продажа|Неправильный размер|Таблица размеров/);
 assert.ok(w.document.querySelector('a[href="/app/instructions?lesson=lesson-1"]'));
 assert.match(w.document.querySelector('#kcList').textContent,/AI продавец/);assert.match(w.document.querySelector('#kcList').textContent,/Вся компания/);assert.match(w.document.querySelector('#kcList').textContent,/Возможно, дубликат/);
 const status=w.document.getElementById('kcStatus');status.value='pending_review';status.dispatchEvent(new w.Event('change'));assert.equal(w.document.querySelectorAll('.kc-card').length,1);assert.match(w.document.querySelector('.kc-card').textContent,/Неправильный размер/);
 w.tenantI18n.language='az';w.document.dispatchEvent(new w.CustomEvent('tenant-language'));assert.equal(w.document.querySelector('[data-knowledge-center-label]').textContent,'Bilik Mərkəzi');
 assert.equal(w.document.querySelector('#knowledgeCenter h1').textContent,'Bilik Mərkəzi','language changes should refresh the complete Knowledge Center shell');
 assert.equal(w.document.getElementById('kcCategory').getAttribute('aria-label'),'Material növü');
 for(let i=0;i<20&&!w.document.querySelectorAll('.kc-card').length;i++)await tick();
 assert.ok(w.document.querySelector('#kcList').textContent.includes('Mümkün dublikat'));
 assert.ok(w.document.querySelector('#kcList').textContent.includes('Bilik mənbəyi'));
 assert.ok(!w.document.querySelector('#kcList').textContent.includes('Knowledge Source'));
 console.log('PASS: tenant-scoped unified sources, instruction/case links, filters and localization');dom.window.close();
})().catch(error=>{console.error(error.stack||error,error.message);dom.window.close();process.exitCode=1;});
