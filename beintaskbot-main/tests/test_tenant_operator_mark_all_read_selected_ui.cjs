const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.join(__dirname,'..'),read=file=>fs.readFileSync(path.join(root,file),'utf8');
const tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-4000-8000-000000000201';
const m1={id:'m1',direction:'incoming',message_type:'text',body:'Synthetic old inbound',author_type:'customer'};
const m2={id:'m2',direction:'incoming',message_type:'text',body:'Synthetic arrival during bulk read',author_type:'customer'};
const row={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'client',customer_kind:'contact',customer_name:'Synthetic Client',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:false,last_message_id:m1.id,last_message_direction:'incoming',last_message_text:m1.body,unread_count:1};
const tick=async()=>{for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));};
const response=result=>({ok:true,status:200,json:async()=>({success:true,result})});
(async()=>{
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window;
 const profile={member:{tenant_id:tenant,telegram_id:33,role:'worker',permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}};
 let unread=1,messages=[m1],readCalls=0,readAllCalls=0,confirmed=0;
 Object.defineProperty(w.document,'hidden',{value:true,configurable:true});w.tenantProfileSnapshot=profile;w.confirm=()=>{confirmed++;return true;};
 w.fetch=async(url,options={})=>{const value=String(url);if(value.includes('/api/platform/me'))return response(profile);
  if(value.includes('/dialogs/read-all')&&options.method==='POST'){readAllCalls++;const body=JSON.parse(options.body);assert.equal(body.expected_tenant_id,tenant);assert.equal(body.expected_user_id,33);unread=1;messages=[m1,m2];return response({updated_dialogs:1,captured_dialogs:1,unread_before:1});}
  if(value.includes('/dialogs/read')&&options.method==='POST'){readCalls++;return response({updated:true});}
  if(value.includes('/dialogs/history'))return response({dialog:{...row,last_message_id:messages.at(-1).id},messages:[...messages],next_before:null,read_through_message_id:messages.at(-1).id});
  if(value.includes('/dialogs?'))return response({dialogs:[{...row,last_message_id:messages.at(-1).id,last_message_text:messages.at(-1).body,unread_count:unread}],channels:[{id:'connect:'+channel,name:'WhatsApp'}],agents:[],attention_count:unread,unread_count:unread,total_count:1,next_offset:null});
  throw Error('Unexpected request '+value);
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await tick();await w.tenantOperator.refresh();await tick();
 await w.tenantOperator.openDialog(row);await tick();assert.equal(readCalls,0,'hidden-page setup does not auto-mark the synthetic baseline message read');
 Object.defineProperty(w.document,'hidden',{value:false,configurable:true});const menu=w.document.getElementById('operatorToolsMenu');menu.open=true;w.document.getElementById('operatorMarkAllRead').click();await tick();await tick();
 assert.equal(confirmed,1);assert.equal(readAllCalls,1);assert.equal(readCalls,0,'the bulk operation refresh does not auto-read a new message in the selected conversation');
 assert.equal(w.document.querySelector('.operator-unread-count')?.textContent,'1','a message arriving after the bulk snapshot remains unread in the selected card');
 assert.match(w.document.getElementById('operatorNotice').textContent,/1/,'success status reports the post-refresh unread count');
 dom.window.close();console.log('PASS: bulk-read refresh preserves a post-snapshot message in the selected visible dialog and reports remaining unread count.');
})().catch(error=>{console.error(error);process.exitCode=1;});
