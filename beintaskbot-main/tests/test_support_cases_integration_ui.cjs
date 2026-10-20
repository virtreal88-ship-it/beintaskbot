const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('fs'),assert=require('node:assert/strict');
const dom=new JSDOM('<div id="supportCases"></div><b id="supportCasesPending"></b>',{url:'https://example.test/app?site_lang=az',runScripts:'outside-only'});
const w=dom.window,calls=[];let page=0;
w.tenantProfileSnapshot={member:{tenant_id:'tenant-a',telegram_id:7,role:'owner'}};
w.fetch=async(url,opt={})=>{calls.push({url,opt});let result={};if(url.includes('/ai/channels')){const q=new URL(url,'https://example.test').searchParams;if(q.get('expected_tenant_id')!=='tenant-a'||q.get('expected_user_id')!=='7')return {ok:false,status:409,json:async()=>({success:false,error_code:'identity_changed'})};}
if(url.includes('/ai/dialogs'))result={dialogs:[{provider:'connect',channel_id:'channel-a',conversation_id:'group@g.us',customer_name:'Group'}]};
else if(url.includes('/ai/agents'))result={agents:[]};
else if(url.includes('/ai/channels'))result={channels:[{provider:'connect',channel_id:'channel-a',name:'First'},{provider:'connect',channel_id:'channel-b',name:'Second'}]};
else if(url.includes('/batches'))result={batch:{id:'batch-a',status:'completed',message_count:29,items:[],continuations:[]}};
else {page++;result={candidates:[{id:'card-'+page,title:'Case '+page,status:'pending_review'}],next_before:page===1?'cursor-a':null};}
return {ok:true,json:async()=>({success:true,result})};};
const tick=()=>new Promise(r=>setTimeout(r,20));
(async()=>{w.eval(fs.readFileSync('docs/tenant-knowledge-candidates.js','utf8'));await tick();
assert.equal(w.document.querySelectorAll('#casesChannel option').length,3);
assert(calls.some(c=>c.url.includes('knowledge-candidates?')&&!c.url.includes('channel_id')),'Unified queue loads without route');
const initialQueueCall=calls.find(c=>c.url.includes('knowledge-candidates?')&&!c.url.includes('channel_id'));assert(!new URL(initialQueueCall.url,'https://example.test').searchParams.has('status'),'default queue leaves rejected filtering to the server');
assert.equal(w.document.getElementById('casesMore').hidden,false);
w.document.getElementById('casesMore').click();await tick();assert(calls.some(c=>c.url.includes('before=cursor-a')));assert.equal(w.document.querySelectorAll('.support-case').length,2);
w.document.getElementById('casesChannel').value='channel-a';w.document.getElementById('casesFrom').value='2026-10-17';w.document.getElementById('casesTo').value='2026-10-18';w.document.getElementById('casesAnalyse').click();await tick();
const post=calls.find(c=>c.opt.method==='POST');assert(post);const body=JSON.parse(post.opt.body);assert.equal(body.channel_id,'channel-a');assert.equal(body.history_mode,'unified_group');assert.equal(body.expected_tenant_id,'tenant-a');assert(body.request_id);assert.equal(body.conversation_id,undefined);
assert(w.document.getElementById('casesBatch').textContent.includes('29'));assert(calls.some(c=>c.url.includes('batch_id=batch-a')));assert(!calls.some(c=>c.url.includes('/approve')));
w.document.getElementById('casesStatus').value='all';w.document.getElementById('casesLoad').click();await tick();assert(calls.some(c=>new URL(c.url,'https://example.test').searchParams.get('status')==='all'),'explicit all-including-rejected filter reaches the API');
w.close();console.log('PASS: UI queue, real channels, keyset paging, bounded batch launch and results; no automatic publication');})().catch(e=>{console.error(e);process.exitCode=1;w.close();});
