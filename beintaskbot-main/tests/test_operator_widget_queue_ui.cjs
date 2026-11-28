const fs = require('node:fs');
const assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const {webcrypto} = require('node:crypto');
const read = name => fs.readFileSync('docs/'+name,'utf8');
const tick = () => new Promise(done=>setTimeout(done,5));
const wait = async predicate => {
  for(let n=0;n<120;n++){if(predicate())return; await tick();}
  assert.fail('UI state did not settle');
};
(async()=>{
  const dom = new JSDOM(read('tenant-app.html'),{url:'https://test.invalid/app?view=dialogs',runScripts:'outside-only'});
  const w=dom.window,d=w.document;
  d.getElementById('view-dialogs').classList.add('active');
  Object.defineProperty(w,'crypto',{value:webcrypto}); w.TextEncoder=TextEncoder;
  w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
  const profile={member:{tenant_id:'demo',telegram_id:11,role:'owner',active:true},
    capabilities:{operator_widget:true,dialog_reply_templates:true,modules:{dialogs:true}}};
  w.localStorage.setItem('tenant-app-language','ru');
  w.tenantProfileSnapshot=profile;
  const site={provider:'operator_widget',channel_id:'instance',conversation_id:'verified-route',lead_id:15,
    channel_name:'Чат сайта BeinSystems',customer_name:'Widget Test',customer_kind:'contact',
    operator_account:'test',dialog_company_name:'test',
    company_status:'unlinked',widget_revision:'a'.repeat(64),available_actions:[],can_send:false,lifecycle_enabled:true,
    can_add_internal_comment:true,
    lifecycle:{state:'waiting',revision:1,last_inbound_id:'2',can_accept:true}};
  assert.equal(d.getElementById('operatorWidget'),null,'there is one inbox in Dialogues');
  const calls=[];let outcome='unknown',removed=false;
  w.fetch=async(url,opt={})=>{
    url=String(url);const data=opt.body&&JSON.parse(opt.body);
    calls.push({url,data});let result;
    if(url.endsWith('/me'))return {ok:true,json:async()=>profile};
    if(url.includes('dialogs_lifecycle')){
      if(data.action==='close'){
        site.lifecycle={state:'closed',operator_id:11,revision:3,can_close:false,can_start:false,last_inbound_id:'2',
          action_block_reason:'widget_session_closed'};
        Object.assign(site,{can_add_internal_comment:true,can_send:false,archive_read_only:true});
      }else{
        site.lifecycle={state:'accepted',operator_id:11,revision:2,can_close:true,can_dismiss:true,last_inbound_id:'2'};
        if(data.action==='start'){site.lifecycle.revision=4;site.archive_read_only=false;}
        site.can_add_internal_comment=true;site.send_block_reason='widget_socket_unavailable';
      }
      result={lifecycle:site.lifecycle};
    }else if(url.includes('dialogs/history')){
      result={dialog:{...site},messages:[{id:'2',body:'test',direction:'incoming'}]};
    }
    else if(url.includes('dialogs?'))result={dialogs:removed?[]:[site],channels:[{id:'operator_widget:instance',name:site.channel_name}],agents:[],total_count:removed?0:1};
    else if(url.endsWith('dialogs/remove')){
      removed=true;
      result={removed, provider:data.provider,channel_id:data.channel_id,conversation_id:data.conversation_id,lead_id:String(data.lead_id)};
    }
    else if(url.includes('dialogs/read'))result={updated:true};
    else if(url.includes('dialogs/internal-comments'))result={comment:{id:'n',note:data.note,at:new Date().toISOString(),employee_name:'Tester'}};
    else if(url.includes('dialogs/reply-templates'))result={available:true,templates:[
      {id:'t',name:'Greeting',body:'Salam!',visibility:'team'}],allowed_visibility:[]};
    else if(url.endsWith('/operator/widget'))return {ok:true,status:200,json:async()=>({success:true,state:outcome})};
    else throw Error('Unexpected '+url);
    return {ok:true,status:200,json:async()=>({success:true,result})};
  };
  for(const f of ['tenant-operator-i18n.js','tenant-dialog-source-tabs.js','tenant-dialog-reply-templates.js','tenant-operator-dialogs.js'])w.eval(read(f));
  await tick();await tick();await w.tenantOperator.openDialog(site);
  const selected=()=>d.querySelector('.dialog-source-tabs [aria-pressed=true]');
  assert.deepEqual([...d.querySelectorAll('.dialog-source-tabs button')].map(b=>b.textContent),['Чат','WhatsApp','Комментарий']);
  assert.equal(selected().textContent,'Чат');
  const closeControl=()=>d.querySelector('[data-operator-lifecycle=close]');
  assert(!closeControl(),'waiting dialogue cannot be completed');
  assert(!d.querySelector('.dialog-source-tabs button:last-child').disabled,'waiting dialogue permits private notes');
  d.getElementById('operatorCompanyPanelOpen').click();
  assert(d.getElementById('operatorCompanyPanel').open);
  assert.match(d.getElementById('operatorClientCompanyHost').textContent,/нет подтверждённой привязки/);
  site.company_status='denied';
  await w.tenantOperator.openDialog(site);
  assert.match(d.getElementById('operatorClientCompanyHost').textContent,/недоступны по вашим правам/);
  Object.assign(site,{company_status:'linked',crm_company_id:'confirmed-company',dialog_company_name:'Verified Company',
    company_details:{email:'synthetic@example.invalid'}});
  await w.tenantOperator.openDialog(site);
  assert.match(d.getElementById('operatorClientCompanyHost').textContent,/Verified Company.*synthetic@example.invalid/);
  Object.assign(site,{company_status:'unlinked',crm_company_id:null,dialog_company_name:'test',company_details:undefined});
  await w.tenantOperator.openDialog(site);
  d.querySelector('[data-operator-lifecycle=accept]').click();
  await wait(()=>!!closeControl());
  assert.equal(selected().textContent,'Чат');
  assert.equal(d.getElementById('operatorDialogCompanyName').textContent,'Компания (Operator) · test');
  assert(!d.getElementById('operatorDialogCompanyName').hidden);
  assert.equal(d.querySelector('#operatorWidgetCompanyRow span').textContent,'test');
  assert(d.getElementById('sendChat').disabled);
  for(const action of ['close','dismiss']){
    const control=d.querySelector('[data-operator-lifecycle="'+action+'"]');
    assert(control,'expected '+action+' on the accepted dialogue');
    assert(!control.closest('[hidden]'),action+' must be visible even when provider sending is blocked');
  }
  assert(d.getElementById('operatorSendReason').textContent.includes('Нет соединения'));
  assert.equal(d.getElementById('chatText').disabled,false);
  d.querySelector('.dialog-source-tabs button:last-child').click();
  const noteClose=closeControl();
  assert(noteClose.closest('.dialog-note-lifecycle-actions'),'close stays at the bottom on the Comment tab');
  assert(!noteClose.closest('#operatorConversationHeader,.dialog-channel-muted,[hidden]'));
  assert(d.querySelector('[data-operator-lifecycle=dismiss]').closest('.dialog-note-lifecycle-actions'));
  assert(d.getElementById('sendChat').closest('.dialog-channel-muted'),'customer reply is not exposed in the note editor');
  d.querySelector('.dialog-source-tabs button:first-child').click();
  assert.equal(closeControl(),noteClose,'switching tabs preserves the same lifecycle button');
  assert(noteClose.closest('.operator-composer-actions'));
  d.querySelector('.dialog-source-tabs button:last-child').click();
  const assertNoteEditor = root => {
    const field=root.querySelector('.dialog-note-composer textarea');
    const send=root.querySelector('.dialog-note-composer button[type=submit]');
    assert(field && send && send.parentElement===field.parentElement,'send icon is inside the input frame');
    assert(send.querySelector('svg[aria-hidden=true]'));
    assert.equal(send.getAttribute('aria-label'),'Сохранить комментарий');
    assert(send.disabled,'empty notes cannot be sent');
    return [field,send];
  };
  const [field,noteSend]=assertNoteEditor(d.querySelector('.dialog-note-form'));
  field.value='staff only';field.dispatchEvent(new w.Event('input'));
  assert(!noteSend.disabled);noteSend.click();
  await wait(()=>calls.some(c=>c.url.includes('internal-comments')));
  assert(!calls.some(c=>c.url.endsWith('/operator/widget')||c.url.includes('/crm/chat/send')));
  site.can_send=true;site.send_block_reason='';
  await w.tenantOperator.openDialog(site);
  d.querySelector('.dialog-source-tabs button').click();
  const input=d.getElementById('chatText');input.value='salam';
  input.dispatchEvent(new w.Event('input',{bubbles:true}));
  const suggestions=()=>d.querySelector('.operator-reply-template-suggestions');
  await wait(()=>suggestions()&&!suggestions().hidden);
  input.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  await wait(()=>d.getElementById('operatorSendReason').textContent.includes('не подтверждена'));
  assert.equal(input.value,'salam');assert(suggestions().hidden);
  assert.equal(calls.filter(c=>c.url.endsWith('/operator/widget')).length,1);
  outcome='pending';input.value='test reply';input.dispatchEvent(new w.Event('input',{bubbles:true}));
  d.getElementById('sendChat').click();
  await wait(()=>calls.some(c=>c.url.endsWith('/operator/widget')));
  await wait(()=>input.value==='');
  assert(d.getElementById('operatorSendReason').textContent.includes('Ответ доставлен'));
  const reply=calls.filter(c=>c.url.endsWith('/operator/widget')).at(-1).data;
  assert.deepEqual([reply.chat_id,reply.text,reply.revision,reply.action],['15','test reply',site.widget_revision,'reply']);
  assert(!calls.some(c=>c.url.includes('/crm/chat/send')));
  const cutoff=calls.length;
  d.querySelector('.dialog-source-tabs button:last-child').click();
  closeControl().click();
  await wait(()=>d.querySelector('[data-widget-action-reason]')?.textContent.includes('Сессия закрыта'));
  assert.equal(w.tenantOperator.currentDialog.lead_id,15);
  assert(!closeControl());
  assert(!d.querySelector('.dialog-source-tabs button:last-child').disabled,'completed dialogue permits private notes');
  assert(d.querySelector('#chatRows [data-message-id="2"]'),'closed history remains visible');
  const afterClose=calls.slice(cutoff);
  assert.deepEqual(afterClose.filter(c=>c.url.includes('dialogs_lifecycle')).map(c=>[
    c.data.action,c.data.provider,c.data.channel_id,c.data.conversation_id,c.data.lead_id,c.data.expected_revision
  ]),[['close','operator_widget','instance','verified-route',15,2]]);
  assert(!afterClose.some(c=>c.url.endsWith('/operator/widget')),'completion does not send a provider message');
  site.lifecycle={...site.lifecycle,can_start:true,action_block_reason:''};
  await w.tenantOperator.openDialog(site);
  const start=d.querySelector('[data-operator-lifecycle=start]');
  assert(start,'a still-open Operator session can resume in the same CRM card');
  start.click();
  await wait(()=>!!closeControl());
  assert.equal(w.tenantOperator.currentDialog.lead_id,15);
  assert.equal(calls.slice(cutoff).filter(c=>c.url.endsWith('/operator/widget')).length,0);
  Object.assign(site,{provider:'connect',archive_read_only:false,channel_id:'wa',conversation_id:'994123456789@s.whatsapp.net',
    lead_id:29,can_send:false,can_add_internal_comment:false,send_block_reason:'dialog_accept_required',
    lifecycle:{state:'waiting',revision:1,last_inbound_id:'5',can_accept:true}});
  await w.tenantOperator.openDialog(site);
  assert.equal(selected().textContent,'WhatsApp');
  d.querySelector('[data-operator-lifecycle=accept]').click();
  await wait(()=>!d.querySelector('[data-operator-lifecycle=accept]'));
  assert.equal(selected().textContent,'WhatsApp');
  site.can_add_internal_comment=true;
  await w.tenantOperator.openDialog(site);
  d.querySelector('[data-operator-internal-comment]').click();
  const modal=d.getElementById('operatorInternalComment');
  const [modalField,modalSend]=assertNoteEditor(modal);
  modalField.value='modal staff only';modalField.dispatchEvent(new w.Event('input'));
  assert(!modalSend.disabled);modalSend.click();
  await wait(()=>!d.getElementById('operatorInternalComment'));
  assert.equal(calls.filter(c=>c.url.includes('internal-comments')).at(-1).data.note,'modal staff only');
  const removeButton=()=>d.querySelector('[data-operator-remove-dialog]');
  assert.equal(removeButton(),null);
  site.can_remove_dialog=true;
  await w.tenantOperator.openDialog(site);
  d.getElementById('operatorMoreActions').open=true;
  assert.equal(removeButton().textContent,'Удалить диалог');
  w.confirm=()=>false;
  removeButton().click();await tick();
  assert(!calls.some(c=>c.url.endsWith('dialogs/remove')));
  w.confirm=()=>true;
  removeButton().click();removeButton().click();
  await wait(()=>d.getElementById('operatorNotice').textContent==='Диалог удалён из списка.');
  assert(!w.tenantOperator.currentDialog);
  const removals=calls.filter(c=>c.url.endsWith('dialogs/remove')).map(c=>c.data);
  assert.equal(removals.length,1);
  assert.deepEqual([removals[0].provider,removals[0].channel_id,removals[0].lead_id,removals[0].expected_tenant_id],['connect','wa',29,'demo']);
  assert.equal(calls.filter(c=>c.url.endsWith('/operator/widget')).length,2);
  assert(!calls.some(c=>c.url.includes('/crm/chat/send')));
  dom.window.close();
  console.log('Widget uses the regular conversation, keeps its source on acceptance, isolates notes and addresses only the selected chat.');
})().catch(err=>{console.error(err);process.exit(1)});
