const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const settle=async()=>{for(let i=0;i<15;i++)await new Promise(resolve=>setImmediate(resolve));};
const channel='00000000-0000-0000-0000-000000000201',anchor='10000000-0000-4000-8000-000000000001';
const sourceUrl='/app?'+new URLSearchParams({view:'dialogs',open_dialog:'1',provider:'connect',lead_id:'99',channel_id:channel,conversation_id:'developers@g.us',source_message_id:anchor});
const operator=fs.readFileSync('docs/tenant-operator-dialogs.js','utf8');
const parser=operator.slice(operator.indexOf('  function notificationDialogRoute(){'),operator.indexOf('  function clearNotificationDialogRoute(){'));
for(const [url,expected] of [[sourceUrl,anchor],[sourceUrl.replace(anchor,'not-a-uuid'),null]]){
 const context={window:{location:{search:new URL(url,'https://crm.test').search}},URLSearchParams};vm.createContext(context);vm.runInContext(parser+';result=notificationDialogRoute();',context);assert.equal(context.result?.source_message_id||null,expected);
}
(async()=>{
 const dom=new JSDOM('<select data-app-language></select><aside id="operatorCopilot"></aside><div id="host"></div>',{url:'https://crm.test/app',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];
 const member={tenant_id:'company-a',telegram_id:11,role:'owner'},route={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'customer',agent_id:'agent-a'};
 const reply={id:'reply-a',consultation_id:'consult-a',source_channel:channel,source_conversation:'customer',customer:'Customer',author:'Developer',body:'[Голосовое сообщение разработчика]',message_type:'audio',draft_state:'not_requested',can_draft:false,source_url:sourceUrl,attachment_url:'/api/platform/crm/chat/attachment?message_id=connect%3Avoice-a',happened_at:'2026-11-01T12:00:00Z'};
 w.tenantOperator={member,currentDialog:route,hasRight:()=>true};w.tenantProfileSnapshot={member};w.tenantI18n={language:'ru',date:String};
 w.fetch=async(url,options={})=>{calls.push({url,options});let result={};if(url.includes('/replies'))result={replies:[reply],consultations:[{id:'consult-a',source_channel:channel,source_conversation:'customer',state:'sent',recipient:'Developers'}],notification_unread_count:0};if(url.startsWith('/api/platform/notifications'))result={notifications:[],unread_count:0};return{ok:true,json:async()=>({success:true,result})};};
 w.eval(fs.readFileSync('docs/tenant-dialog-consultations.js','utf8'));await settle();
 const helper=d.querySelector('#dialogConsultationReplies');assert.equal([...helper.querySelectorAll('a')].find(link=>link.getAttribute('href')===sourceUrl)?.textContent,'Открыть сообщение разработчика',helper.outerHTML+' '+JSON.stringify(calls));assert.equal(helper.querySelector('a[href^="/api/platform/crm/chat/attachment"]').target,'_blank');
 const ai=[...helper.querySelectorAll('button')].find(node=>node.textContent.includes('AI-черновик'));assert.ok(ai?.disabled,'unread developer media cannot start paid AI');
 w.eval(fs.readFileSync('docs/tenant-support-consultations.js','utf8'));const controller=w.tenantCaseConsultations.mount({host:d.querySelector('#host'),ticket:{id:'case-a',...route,status:'new'},member,getMessages:()=>[],isCurrent:()=>true});await settle();
 assert.ok(d.querySelector('#host a[href^="/api/platform/crm/chat/attachment"]'));
 assert.equal(d.querySelectorAll('audio,video,img').length,0,'media replies are lazy links, never loaded on read');assert.equal(calls.some(call=>call.url.startsWith('/api/platform/crm/chat/attachment')),false);
 controller.dispose();dom.window.close();console.log('PASS: protected developer media links, exact source-message anchor and no automatic media/AI loading');
})().catch(error=>{console.error(error);process.exit(1);});
