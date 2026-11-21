const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.join(__dirname,'..'),read=file=>fs.readFileSync(path.join(root,file),'utf8');
const tick=async()=>{for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
 await verifyRealDialogComposers();
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?view=dialogs',runScripts:'outside-only'});
 const w=dom.window,tenantA='00000000-0000-4000-8000-000000000101',tenantB='00000000-0000-4000-8000-000000000102';
 const profile=tenant=>({member:{tenant_id:tenant,telegram_id:33,role:'worker',active:true,permissions:['dialogs']},capabilities:{modules:{dialogs:true},dialog_reply_templates:tenant===tenantA}});
 w.tenantProfileSnapshot=profile(tenantA);w.tenantI18n={language:'en'};
 const view=w.document.getElementById('view-dialogs');view.classList.add('active');
 const host=w.document.createElement('div');host.id='operatorConversationContent';view.append(host);
 const shell=w.document.createElement('section');shell.dataset.operatorDialog='';host.append(shell);
 const composer=w.document.createElement('div');composer.className='operator-composer';
 const chat=w.document.createElement('textarea');chat.id='chatText';chat.dataset.operatorComposer='';composer.append(chat);
 const actions=w.document.createElement('div');actions.className='operator-composer-actions';composer.append(actions);
 const sendButton=w.document.createElement('button');sendButton.id='sendChat';sendButton.type='button';actions.append(sendButton);
 shell.append(composer);
 const dealComposer=w.document.createElement('div');dealComposer.className='tc-legacy-composer operator-composer';
 const dealChat=w.document.createElement('textarea');dealChat.id='chatText';dealComposer.append(dealChat);
 const dealActions=w.document.createElement('div');dealActions.className='operator-composer-actions';dealComposer.append(dealActions);
 w.document.body.append(dealComposer);
 // Make the host's fallback deterministic even if the CI machine uses another locale.
 w.eval(`{
   const lowercase=String.prototype.toLocaleLowerCase;
   String.prototype.toLocaleLowerCase=function(locales='en-US'){return lowercase.call(this,locales);};
 }`);
 assert.equal(w.eval("'QIŞ'.toLocaleLowerCase()"),'qiş','the browser defaults to en-US casing');
 const initial=[
   {id:'t1',name:'Welcome',body:'Welcome back. How can I help?',visibility:'team',version:1,can_edit:true},
   {id:'az1',name:'QİYMƏT',body:'Məlumat.',visibility:'personal',version:1,can_edit:false},
   {id:'az2',name:'Çatdırılma',body:'QIŞ üçün çatdırılma.',visibility:'personal',version:1,can_edit:false},
   {id:'ru1',name:'ДОСТАВКА',body:'Условия отправки.',visibility:'personal',version:1,can_edit:false},
   {id:'en1',name:'INSTALL guide',body:'Setup steps.',visibility:'personal',version:1,can_edit:false},
   {id:'en2',name:'Software setup',body:'How to install the application.',visibility:'personal',version:1,can_edit:false},
   {id:'az3',name:'dilimiz',body:'Birinci nümunə.',visibility:'personal',version:1,can_edit:false},
   {id:'az4',name:'dılımız',body:'İkinci nümunə.',visibility:'personal',version:1,can_edit:false},
   {id:'marked',name:'SA\u0301LON',body:'Distinct spelling.',visibility:'personal',version:1,can_edit:false}
 ];
 const calls=[];let sent=0,storageWrites=0;
 Object.defineProperty(w.localStorage,'setItem',{value:()=>{storageWrites++;}});
 w.document.getElementById('sendChat')?.addEventListener('click',()=>sent++);
 w.fetch=async(url,options={})=>{
   const value=String(url);calls.push({url:value,method:options.method||'GET',body:options.body||''});
   if(!value.includes('/api/platform/ai/dialogs/reply-templates'))throw new Error('unexpected non-template request: '+value);
   if((options.method||'GET')==='GET'){
     const tenant=new URL(value,'https://crm.test').searchParams.get('expected_tenant_id');
     if(tenant===tenantB)return{ok:false,status:404,json:async()=>({success:false,error:'Unavailable'})};
     return{ok:true,json:async()=>({success:true,result:{available:true,templates:initial,allowed_visibility:['personal','shared']}})};
   }
   const body=JSON.parse(options.body);
   return{ok:true,json:async()=>({success:true,result:{id:'t2',name:body.name,body:body.body,visibility:body.visibility,version:1,can_edit:true}})};
 };
 const script=read('docs/tenant-dialog-reply-templates.js');
 w.eval(script);
 assert.equal(w.document.querySelector('#operatorReplyTemplatesOpen')?.hidden,true,'pilot control stays hidden until the server confirms workspace eligibility');
 await tick();
 const input=w.document.getElementById('chatText'),button=w.document.getElementById('operatorReplyTemplatesOpen');
 assert.ok(button,'template control is attached to the existing composer');
 assert.equal(dealActions.querySelector('#operatorReplyTemplatesOpen'),null,'decoy deal composer with overlapping IDs is not mounted');
 assert.equal(calls.length,1,'templates are loaded once for the current tenant');
 input.value='welcome';input.dispatchEvent(new w.Event('input',{bubbles:true}));await tick();
 assert.equal(calls.length,1,'typing filters in memory and never sends draft text to an API');
 const suggestion=w.document.querySelector('.operator-reply-template-suggestions button');
 assert.ok(suggestion,'a matching template is suggested');
 suggestion.click();await tick();
 assert.match(input.value,/Welcome back/,'selection inserts text into the editable composer');
 assert.equal(sent,0,'selection never sends a message');
 assert.equal(storageWrites,0,'templates and drafts are not stored in localStorage');
 const type=query=>{input.value=query;input.dispatchEvent(new w.Event('input',{bubbles:true}));};
 const matches=()=>[...w.document.querySelectorAll('.operator-reply-template-suggestions button span')].map(node=>node.textContent);
 w.tenantI18n.language='az';
 type('qiymət');
 assert.deepEqual(matches(),['QİYMƏT'],'AZ dotted lowercase i matches AZ dotted uppercase İ under en-US');
 type('qış');
 assert.deepEqual(matches(),['Çatdırılma'],'AZ dotless lowercase ı matches uppercase I in template body under en-US');
 w.document.querySelector('.operator-reply-template-suggestions button').click();
 assert.equal(input.value,'qış\nQIŞ üçün çatdırılma.','AZ selection only appends original text to the editable draft');
 type('qiş');
 assert.deepEqual(matches(),['Çatdırılma'],'ambiguous uppercase I can match through alternate casing');
 w.tenantI18n.language='ru';
 type('доставка');
 assert.deepEqual(matches(),['ДОСТАВКА'],'Russian remains case-insensitive');
 type('qiymət');
 assert.deepEqual(matches(),['QİYMƏT'],'AZ dotted name works with Russian menus');
 type('qış');
 assert.deepEqual(matches(),['Çatdırılma'],'AZ dotless body works with Russian menus');
 type('dilimiz');
 assert.deepEqual(matches(),['dilimiz'],'fallback does not turn lower-case dotless ı into dotted i');
 type('dılımız');
 assert.deepEqual(matches(),['dılımız'],'fallback does not turn lower-case dotted i into dotless ı');
 type('salon');
 assert.deepEqual(matches(),[],'fallback does not strip combining marks');
 w.tenantI18n.language='az';
 type('install');
 assert.deepEqual(matches(),['Software setup','INSTALL guide'],
   'selected-locale body matches rank ahead of fallback title matches');
 w.tenantI18n.language='en';
 type('welcome');
 assert.deepEqual(matches(),['Welcome'],'English remains case-insensitive after a language change');
 assert.equal(calls.length,1,'localized filtering and insertion never call the server');
 assert.equal(sent,0,'localized insertion never sends a message');
 button.click();await tick();
 assert.ok(w.document.getElementById('operatorReplyTemplateModal'),'management is available in the modal');
 const edit=[...w.document.querySelectorAll('#operatorReplyTemplateModal button')].find(node=>node.textContent==='Edit');
 assert.ok(edit);edit.click();
 const editForm=w.document.querySelector('#operatorReplyTemplateModal form');
 const editVisibility=editForm.querySelector('[name=visibility]');
 assert.equal(editVisibility.value,'team','an all-scope editor without a department keeps the existing team scope selected');
 assert.deepEqual([...editVisibility.options].map(option=>option.value).sort(),['personal','shared','team']);
 editForm.querySelector('[name=name]').value='Welcome edited';
 editForm.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
 const editPost=calls.find(call=>call.method==='POST');
 assert.equal(JSON.parse(editPost.body).visibility,'team','editing does not silently reassign the template to another scope');
 button.click();await tick();
 const add=[...w.document.querySelectorAll('#operatorReplyTemplateModal button')].find(node=>node.textContent==='New template');
 assert.ok(add);add.click();
 const form=w.document.querySelector('#operatorReplyTemplateModal form');
 form.querySelector('[name=name]').value='Follow up';
 form.querySelector('[name=body]').value='Please share a suitable time.';
 form.querySelector('[name=visibility]').value='personal';
 form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
 const post=calls.filter(call=>call.method==='POST').at(-1);
 assert.ok(post,'management uses only the same-origin template API');
 const body=JSON.parse(post.body);
 assert.equal(body.action,'create');assert.equal(body.expected_tenant_id,tenantA);
 assert.equal(body.body,'Please share a suitable time.');
 assert.ok(!Object.keys(body).some(key=>/message|conversation|operator/i.test(key)),'request contains template metadata only');
 assert.equal(sent,0,'creating a template never sends a customer message');
 const callsBeforeDisabledTenant=calls.length;
 w.tenantProfileSnapshot=profile(tenantB);
 w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:w.tenantProfileSnapshot}));await tick();await tick();
 assert.equal(calls.length,callsBeforeDisabledTenant,'templates feature is disabled for the next tenant and emits no API request');
 assert.equal(w.document.querySelector('#operatorReplyTemplatesOpen'),null,'pilot control is hidden outside the configured BeinSystems workspace');
 input.value='welcome';input.dispatchEvent(new w.Event('input',{bubbles:true}));await tick();
 assert.equal(w.document.querySelector('.operator-reply-template-suggestions button'),null,'old tenant templates are cleared');
 w.document.dispatchEvent(new w.CustomEvent('tenant-chat-close'));await tick();
 assert.equal(w.document.getElementById('operatorReplyTemplateModal'),null,'chat close dismisses template management');
 assert.equal(sent,0);
 dom.window.close();
 console.log('PASS: local suggestions and management never send messages or transmit draft text.');
})().catch(error=>{console.error(error);process.exitCode=1;});

async function verifyRealDialogComposers(){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
 try{
  d.getElementById('view-dialogs').classList.add('active');
  const profile={member:{tenant_id:'tenant',telegram_id:33,role:'worker',permissions:['dialogs'],active:true},
    capabilities:{modules:{dialogs:true},dialog_reply_templates:true}};
  w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
  let sendBindings=0;
  w.tenantChatSend={bind(){sendBindings++;}};
  const direct={provider:'connect',lead_id:41,channel_id:'canal',conversation_id:'direct',customer_kind:'contact',can_send:true,state:'human',
    lifecycle_enabled:true,lifecycle:{state:'accepted',operator_id:33,revision:1,can_close:true}};
  const group={...direct,lead_id:42,conversation_id:'group@g.us',customer_kind:'group'};
  const kommo={...direct,provider:'kommo',lead_id:43,channel_id:'',conversation_id:'',lifecycle_enabled:false,lifecycle:null};
  const waiting={...direct,lead_id:44,conversation_id:'waiting',can_send:false,send_block_reason:'dialog_accept_required',
    lifecycle:{state:'waiting',revision:1,can_accept:true,last_inbound_id:'m'}};
  const routes=[direct,group,kommo,waiting],calls=[];
  w.fetch=async(url,opt={})=>{
   const path=new URL(String(url),w.location.origin),method=opt.method||'GET';calls.push([path.pathname,method]);
   if(path.pathname==='/api/platform/me')return{ok:true,json:async()=>profile};
   let result;
   if(path.pathname.endsWith('/dialogs/reply-templates')){
    assert.equal(method,'GET','actual chat never performs template write merely by typing/inserting');
    result={available:true,templates:[{id:'welcome',name:'Welcome',body:'Welcome back.',visibility:'personal'}],allowed_visibility:['personal']};
   }else if(path.pathname.endsWith('/dialogs/history')){
    result={dialog:routes.find(row=>String(row.lead_id)===path.searchParams.get('lead_id')),messages:[],next_before:null};
   }else if(path.pathname.endsWith('/dialogs'))result={dialogs:routes,channels:[],agents:[],next_offset:null};
   else if(path.pathname.endsWith('/dialogs/preferences'))result={};
   else throw Error('Unexpected request '+path.pathname);
   return{ok:true,status:200,json:async()=>({success:true,result})};
  };
  w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-dialog-reply-templates.js'));w.eval(read('docs/tenant-operator-dialogs.js'));
  w.tenantI18n.setLanguage('en');d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));
  await w.tenantOperator.refresh();
  for(const route of routes){
   assert(await w.tenantOperator.openDialog(route));await tick();
   const composer=d.querySelector('#operatorConversationContent [data-operator-dialog] .operator-composer');
   const input=composer.querySelector('[data-operator-composer]'),button=composer.querySelector('#operatorReplyTemplatesOpen');
   assert(button&&!button.hidden,route.provider+' '+route.customer_kind+' real rendered composer has Templates');
   assert.equal(input.disabled,!route.can_send);
   input.value='welcome - draft';input.dispatchEvent(new w.Event('input',{bubbles:true}));await tick();
   const suggestion=composer.querySelector('.operator-reply-template-suggestions button');
   if(route.can_send){
    assert(suggestion);
    if(route===direct){
     input.readOnly=true;suggestion.click();
     assert.equal(input.value,'welcome - draft','stale suggestion respects readonly immediately');
     await tick();assert(composer.querySelector('.operator-reply-template-suggestions').hidden);
     input.readOnly=false;await tick();
    }
    composer.querySelector('.operator-reply-template-suggestions button').click();
    assert.equal(input.value,'welcome - draft\nWelcome back.');
   }else{
    assert(!suggestion);button.click();await tick();
    const use=d.querySelector('#operatorReplyTemplateModal [data-reply-template-insert]');
    assert(use.disabled);use.dispatchEvent(new w.Event('click',{bubbles:true}));
    assert.equal(input.value,'welcome - draft','even a forced click cannot change disabled draft');
   }
  }
  assert.equal(sendBindings,3,'fixture initialized sending only on the three authorized composers');
  assert(!calls.some(([path])=>path.includes('/send')),'templates insert only, never call customer send');
 }finally{dom.window.close();}
}
