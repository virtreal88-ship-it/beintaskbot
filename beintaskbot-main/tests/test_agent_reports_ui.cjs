const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');const fs=require('fs'),assert=require('assert/strict');
const code=fs.readFileSync(__dirname+'/../docs/tenant-agent-reports.js','utf8');
const settle=()=>new Promise(r=>setTimeout(r,10));
(async()=>{
 const dom=new JSDOM('<section class="view active"><div id="agentReports"></div></section>',{runScripts:'outside-only',url:'https://crm.test/app'}),w=dom.window,d=w.document;
 w.tenantProfileSnapshot={member:{tenant_id:'A',telegram_id:11}};w.tenantI18n={language:'ru'};
 let pending;const calls=[];const answer=()=>({ok:true,json:async()=>({success:true,result:{tenant_id:'A',period:{from:'2026-11-15',to:'2026-11-15'},agents:[{id:'a',name:'Day <agent>'}],facts:[{agent_id:'a',state:'completed',count:3}],selected_agent_id:null}})});
 w.fetch=async(u,o)=>{calls.push([u,o]);return pending?new Promise(r=>pending=r):answer()};w.eval(code);await settle();
 assert.match(d.querySelector('#arResult').textContent,/Day <agent>/);assert.equal(d.querySelector('#arResult agent'),null);
 d.dispatchEvent(new w.Event('tenant-auth-invalid'));assert.equal(d.querySelector('#arResult').textContent,'');assert.equal(d.querySelector('#arAgent').options.length,1);d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:w.tenantProfileSnapshot}));await settle();
 pending=true;d.querySelector('form').dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();
 d.dispatchEvent(new w.Event('tenant-auth-pending')); pending(answer());await settle();assert.equal(d.querySelector('#arResult').textContent,'');
 assert(calls.every(([,o])=>!o.method&&o.cache==='no-store'));w.close();console.log('PASS agent reports escaped facts and stale response suppression');
})().catch(e=>{console.error(e);process.exit(1)});
