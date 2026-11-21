const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,10));
const debounce=()=>new Promise(r=>setTimeout(r,450));
const read=p=>fs.readFileSync(p,'utf8');
(async()=>{
  const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?view=dialogs',runScripts:'outside-only'});
  const w=dom.window,d=w.document,$=s=>d.querySelector(s);
  $('#view-dialogs').classList.add('active');
  const profile={member:{tenant_id:'tenant',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true}},integrations:[]};
  const group=(lead,ch)=>({provider:'connect',lead_id:lead,channel_id:ch,channel_name:ch,channel_active:true,
    conversation_id:'same-group@g.us',customer_kind:'group',customer_name:'Synthetic group',state:'human',can_send:false});
  const a=group(41,'a'),b=group(42,'b');
  const kommo={...a,provider:'kommo',lead_id:43,channel_id:'k',conversation_id:'provider-peer',customer_kind:'contact'};
  const kommoWithoutJob={...kommo,lead_id:44,channel_id:'',conversation_id:''};
  const operatorCandidate={...kommoWithoutJob,lead_id:45};
  const all=[a,b,kommo,kommoWithoutJob,operatorCandidate];
  const named=(name,manage=false)=>({company_id:name,company_name:name,can_manage:manage});
  const bindings=new Map([[41,named('Company A',true)],[42,named('Company B')],[43,named('Company K',true)],[44,named('Company without job',true)],[45,{company_id:null,can_manage:true}]]);
  const calls=[];let delayRead=false,delaySearch=false,releaseRead,releaseSearch,holdOperator=false,releaseOperator;
  w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.alert=()=>{};w.confirm=()=>true;
  w.fetch=async(url,opt={})=>{
    const u=new URL(String(url),w.location.origin),body=opt.body?JSON.parse(opt.body):null;
    assert.equal(u.origin,w.location.origin,'no external Operator requests');
    calls.push({path:u.pathname,query:u.searchParams,body});let result;
    if(u.pathname==='/api/platform/me')return{ok:true,json:async()=>profile};
    if(u.pathname==='/api/platform/operator/dialog-company'){
      const q=body||Object.fromEntries(u.searchParams),route=all.find(r=>String(r.lead_id)===q.lead_id);
      assert.deepEqual([q.provider,q.channel_id,q.conversation_id],[route.provider,route.channel_id,route.conversation_id]);
      assert.deepEqual([q.expected_tenant_id,q.expected_user_id],['tenant','33']);
      if(body&&'operator_search' in q){
        assert.equal('operator_account' in q,false);
        assert.equal('company_id' in q,false);
        if(holdOperator)await new Promise(resolve=>{releaseOperator=resolve;});
        return{ok:true,json:async()=>({success:true,accounts:[{account:'acct_001',name:'Not contracted',phone:'Not contracted phone'},{account:'@acct_002'}]})};
      }
      if(body&&'operator_account' in q){
        assert.equal('operator_search' in q,false);assert.equal('company_id' in q,false);
        assert.equal(q.operator_account,'acct_001');bindings.set(route.lead_id,named('Bound Operator company',true));
      }else if(body){bindings.set(route.lead_id,q.company_id?named('<b>New company</b>',true):{company_id:null,can_manage:true});}
      if(delayRead&&route===a)await new Promise(resolve=>{releaseRead=resolve;});
      return{ok:true,json:async()=>({success:true,binding:bindings.get(route.lead_id)})};
    }
    if(u.pathname==='/api/platform/crm/native/companies'){
      if(delaySearch)await new Promise(resolve=>{releaseSearch=resolve;});
      return{ok:true,json:async()=>({success:true,companies:[
        {id:'visible-only',name:'Read only',actions:{update:false}},
        {id:'writeable',name:'Can bind',actions:{update:true}}
      ]})};
    }
    if(u.pathname.endsWith('/dialogs/history')){
      const route=all.find(r=>String(r.lead_id)===u.searchParams.get('lead_id'));
      result={dialog:route,messages:[],next_before:null};
    }else if(u.pathname.endsWith('/dialogs'))result={dialogs:[{...a,group_sources:[a,b]},kommo,kommoWithoutJob,operatorCandidate],channels:[],agents:[],next_offset:null};
    else if(u.pathname.endsWith('/dialogs/preferences'))result={};
    else if(u.pathname.endsWith('/dialogs/read'))result={updated:true};
    else throw Error('Unexpected request '+u.pathname);
    return{ok:true,status:200,json:async()=>({success:true,result})};
  };
  w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-dialog-company.js'));w.eval(read('docs/tenant-operator-dialogs.js'));
  w.tenantI18n.setLanguage('en');
  const announce=()=>d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));
  const value=()=> $('[data-company-value]')?.textContent;
  const section=()=> $('[data-operator-dialog-company]');
  const source=async ix=>{const picker=$('#operatorComposerSourceSelect');picker.value=String(ix);picker.dispatchEvent(new w.Event('change'));await tick();await tick();};
  announce();await w.tenantOperator.refresh();await w.tenantOperator.openDialog(kommo);await tick();
  assert(!section()&&!calls.some(c=>c.path.endsWith('/dialog-company')),'no capability means no UI or read');
  profile.capabilities={...profile.capabilities,dialog_company_binding:true};announce();await w.tenantOperator.refresh();
  await w.tenantOperator.openDialog(kommo);await tick();
  assert.equal(value(),'Company K',JSON.stringify({current:w.tenantOperator.currentDialog,
    visible:$('#view-dialogs')?.className,viewCalls:calls.map(c=>c.path).slice(-12)}));
  assert.equal($('#operatorClientCard'),null,'Kommo company access never creates a display-name form');
  section().querySelector('button').click();
  let panel=$('#operatorDialogueCompanyPicker');assert(panel.open);
  const submit=()=>panel.querySelector('form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
  submit();await tick();
  assert.deepEqual([...panel.querySelector('select').options].map(o=>o.value),['','writeable']);
  assert.equal(calls.filter(c=>c.body&&c.path.endsWith('/dialog-company')).length,0,'search is read-only');
  const selection=panel.querySelector('select');selection.value='writeable';selection.dispatchEvent(new w.Event('change'));
  panel.querySelector('button.primary').click();await tick();await tick();
  assert.equal(value(),'<b>New company</b>');assert.equal(section().querySelector('b'),null);
  await w.tenantOperator.openDialog(kommoWithoutJob);await tick();
  assert.equal(value(),'Company without job','exact empty Kommo fields still mount');
  const emptyRead=calls.filter(c=>c.path.endsWith('/dialog-company')&&!c.body).at(-1);
  assert.equal(emptyRead.query.get('conversation_id'),'');assert.equal(emptyRead.query.get('channel_id'),'');
  section().querySelector('button').click();panel=$('#operatorDialogueCompanyPicker');
  const oldHost=section().parentElement,readCount=calls.filter(c=>c.path.endsWith('/dialog-company')).length;
  panel.querySelector('input').value='Unsubmitted search';
  w.tenantI18n.setLanguage('ru');await tick();
  assert.notEqual(section().parentElement,oldHost,'full header rerender occurred');
  assert.equal($('#operatorDialogueCompanyPicker'),panel,'same-route redraw preserves the open picker');
  assert.equal(panel.querySelector('input').value,'Unsubmitted search');
  assert.equal(calls.filter(c=>c.path.endsWith('/dialog-company')).length,readCount,'no duplicate company request while picker stays open');
  w.tenantI18n.setLanguage('en');await tick();
  await w.tenantOperator.openDialog(a);await tick();assert.equal(value(),'Company A');assert($('#operatorClientCard'),'existing Connect roster/name card retained');
  section().querySelector('button').click();panel=$('#operatorDialogueCompanyPicker');delaySearch=true;submit();await tick();assert(releaseSearch);
  await source(1);assert.equal(value(),'Company B');assert(!$('#operatorDialogueCompanyPicker'));
  releaseSearch();await tick();assert(!$('#operatorDialogueCompanyPicker'),'late search cannot resurrect old group source picker');
  assert(section().querySelector('button').hidden,'read-only member has no binding control');
  delayRead=true;await source(0);assert(releaseRead);
  await source(1);assert.equal(value(),'Company B');releaseRead();await tick();
  assert.equal(value(),'Company B','late source A binding never bleeds into source B');
  bindings.set(41,{company_id:null,company_name:'Forbidden company name',can_manage:false});
  delayRead=false;await source(0);
  assert.equal(value(),'Not specified or unavailable');
  assert(!section().outerHTML.includes('Forbidden company name'),'masked company name is absent from text, title and data attributes');
  assert(section().querySelector('button').hidden);
  assert.equal(calls.filter(c=>c.body&&c.path.endsWith('/dialog-company')).length,1);
  await w.tenantOperator.openDialog(operatorCandidate);await tick();
  const searchCalls=()=>calls.filter(c=>c.body?.operator_search);
  const writes=()=>calls.filter(c=>c.body?.operator_account);
  section().querySelector('button').click();
  const pop=()=>$('#operatorDialogueAccountPicker');
  assert(!pop()&&$('#operatorDialogueCompanyPicker').open,'default-off owner gets only local CRM picker');
  assert.equal(searchCalls().length,0);
  await w.tenantOperator.openDialog(b);await tick();
  bindings.set(45,{company_id:null,can_manage:true,can_search_operator:true});
  await w.tenantOperator.openDialog(operatorCandidate);await tick();
  section().querySelector('button').click();
  assert(pop()&&d.activeElement===pop().querySelector('input'));
  assert.equal(searchCalls().length,0);
  pop().querySelector('[data-local-company-picker]').click();
  assert(!pop()&&$('#operatorDialogueCompanyPicker').open,'existing CRM fallback is one click');
  $('#operatorDialogueCompanyPicker').querySelector('[type=button].primary').nextElementSibling.nextElementSibling.click();
  section().querySelector('button').click();
  const type=value=>{const input=pop().querySelector('input');input.value=value;input.dispatchEvent(new w.Event('input'));};
  type('abc');await debounce();assert.equal(searchCalls().length,0);
  type('acct');type(' acct_ ');await debounce();
  assert.equal(searchCalls().length,1);assert.equal(searchCalls()[0].body.operator_search,'acct_');
  assert.equal(searchCalls()[0].body.conversation_id,'');assert.equal(searchCalls()[0].body.channel_id,'');
  assert.deepEqual([...pop().querySelectorAll('[data-operator-account-results] strong')].map(n=>n.textContent),['@acct_001','@acct_002']);
  assert(!pop().textContent.includes('Not contracted'));
  pop().querySelector('input').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  assert.equal(writes().length,0,'typing, incoming results and Enter without a choice never bind');
  holdOperator=true;type('stale');await debounce();assert(releaseOperator);
  await w.tenantOperator.openDialog(b);await tick();assert(!pop());
  releaseOperator();holdOperator=false;await tick();assert(!pop(),'late search cannot repaint a superseded route');
  await w.tenantOperator.openDialog(operatorCandidate);await tick();
  section().querySelector('button').click();type('acct');await debounce();
  const pick=pop().querySelector('[data-operator-account-results] button');pick.click();pick.click();
  await tick();await tick();
  assert.equal(writes().length,1);assert.equal(writes()[0].body.lead_id,'45');
  assert.equal(value(),'Bound Operator company');assert(!pop());
  assert(!calls.some(c=>c.path.includes('/send')));
  d.dispatchEvent(new w.CustomEvent('tenant-auth-pending'));assert(!section(),'auth transition immediately clears company');
  w.close();console.log('PASS: Company field and Operator chooser, exact Kommo/group routes, debounce, explicit bind, fallback, stale guards');
})().catch(err=>{console.error(err);process.exitCode=1;});
