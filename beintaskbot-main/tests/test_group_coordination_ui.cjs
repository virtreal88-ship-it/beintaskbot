const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const read=p=>fs.readFileSync(path.join(__dirname,'..',p),'utf8');
const settle=async()=>{for(let i=0;i<20;i++)await new Promise(r=>setImmediate(r));};
(async()=>{
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?site_lang=ru',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];
 const member={tenant_id:'tenant-a',telegram_id:11,role:'owner',permissions:['dialogs','support'],active:true};
 w.tenantProfileSnapshot={member,capabilities:{modules:{dialogs:true,support:true}}};
 const group={group_jid:'120363431037910651@g.us',revision:7,state:'paused',reason:'',primary_agent_id:'agent-a',primary_agent_name:'Support',can_configure:true,assignments:[{agent_id:'agent-a',channel_id:'channel-a',priority:10}],channels:[{channel_id:'channel-a',lead_id:41,channel_name:'Main'},{channel_id:'channel-b',lead_id:42,channel_name:'Support'}],agents:[{id:'agent-a',name:'<b>Support</b>',scopes:[{provider:'connect',channel_id:'channel-a',allow_group_messages:true}]}]};
 const route={provider:'connect',channel_id:'channel-a',conversation_id:group.group_jid,lead_id:41,customer_name:'Group',channel_name:'Main',state:'paused',display_state:'paused',controls_allowed:true,agent_id:'agent-a',can_send:true,available_actions:['resume'],group_coordination:group};
 w.fetch=async(url,opts={})=>{const u=new URL(url,w.location),body=opts.body?JSON.parse(opts.body):null;calls.push({path:u.pathname,query:u.search,body});let result={};if(u.pathname.endsWith('/dialogs/history'))result={dialog:{...route,channel_id:u.searchParams.get('channel_id')||route.channel_id},messages:[],next_before:null};else if(u.pathname.endsWith('/dialogs'))result={dialogs:[route],channels:[],agents:[],attention_count:1};return{ok:true,status:200,json:async()=>({success:true,result:structuredClone(result)})};};
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await settle();await w.tenantOperator.openDialog(route);await settle();
 assert.ok(d.querySelector('[data-operator-create-ticket]'));assert.ok(d.querySelector('[data-operator-action="resume"]'));
 const section=d.querySelector('.operator-assignment-form');assert.equal(section.querySelector('select').options.length,2);
 section.querySelector('button').click();const form=section.querySelector('form');assert.equal(form.querySelectorAll('select').length,2);assert.equal(form.querySelector('b'),null);
 form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle();
 const writes=calls.filter(x=>x.body);assert.equal(writes.length,1);assert.ok(writes[0].path.endsWith('/group-coordination'));assert.equal(writes[0].body.expected_revision,7);assert.deepEqual(writes[0].body.assignments,[{agent_id:'agent-a',channel_id:'channel-a',priority:10}]);
 for(const lang of ['ru','az','en']){w.tenantI18n.setLanguage(lang);assert.notEqual(w.tenantI18n.t('groupConfigure'),'groupConfigure');assert.notEqual(w.tenantI18n.t('groupHint'),'groupHint');}
 const selector=d.querySelector('.operator-assignment-form select');selector.value='channel-b';selector.dispatchEvent(new w.Event('change'));await settle();assert.ok(calls.some(c=>c.path.endsWith('/history')&&c.query.includes('channel-b')));
 assert.equal(calls.filter(c=>c.body).length,1,'viewing channels and language changes never mutate AI');
 dom.window.close();console.log('Group UI: scoped history, primary/channel form, CAS, rights-driven controls and RU/AZ/EN passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
