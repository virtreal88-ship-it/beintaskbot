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
  const calls=[];let delayRead=false,delaySearch=false,releaseRead,releaseSearch,holdOperator=false,releaseOperator,failedOperator=false;
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
        assert.equal(q.operator_account,'acct_001');
        if(failedOperator)return{ok:false,status:409,json:async()=>({success:false})};
        bindings.set(route.lead_id,{...named('Bound Operator company',true),can_search_operator:true});
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
  const search=()=>$('#operatorDialogueCompanySearch');
  const openSearch=()=>{search().focus();search().click();};
  const source=async ix=>{const picker=$('#operatorComposerSourceSelect');picker.value=String(ix);picker.dispatchEvent(new w.Event('change'));await tick();await tick();};
  announce();await w.tenantOperator.refresh();await w.tenantOperator.openDialog(kommo);await tick();
  assert(!section()&&!calls.some(c=>c.path.endsWith('/dialog-company')),'no capability means no UI or read');
  profile.capabilities={...profile.capabilities,dialog_company_binding:true};announce();await w.tenantOperator.refresh();
  await w.tenantOperator.openDialog(kommo);await tick();
  assert.equal(value(),'Company K',JSON.stringify({current:w.tenantOperator.currentDialog,
    visible:$('#view-dialogs')?.className,viewCalls:calls.map(c=>c.path).slice(-12)}));
  assert.equal($('#operatorConversationName'),null,'Kommo company access never creates a display-name form');
  $('#operatorClientCardOpen').click();
  openSearch();
  let panel=$('#operatorDialogueCompanyPicker');assert.equal(panel.closest('dialog').id,'operatorClientCard');
  const pickLocal=()=>[...panel.querySelectorAll('[data-local-company-results] button')];
  await tick();
  assert.deepEqual(pickLocal().map(b=>b.textContent),['Can bind']);
  assert.equal(panel.querySelectorAll('select,button.primary,form').length,0);
  assert.equal(section().querySelectorAll(':scope > button').length,0,'company search is directly visible');
  assert.equal(calls.filter(c=>c.body&&c.path.endsWith('/dialog-company')).length,0,'search is read-only');
  pickLocal()[0].click();await tick();await tick();
  assert.equal(value(),'<b>New company</b>');assert.equal(section().querySelector('b'),null);
  await w.tenantOperator.openDialog(kommoWithoutJob);await tick();
  assert.equal(value(),'Company without job','exact empty Kommo fields still mount');
  const emptyRead=calls.filter(c=>c.path.endsWith('/dialog-company')&&!c.body).at(-1);
  assert.equal(emptyRead.query.get('conversation_id'),'');assert.equal(emptyRead.query.get('channel_id'),'');
  openSearch();panel=$('#operatorDialogueCompanyPicker');
  const oldHost=section().parentElement,readCount=calls.filter(c=>c.path.endsWith('/dialog-company')).length;
  panel.querySelector('input').value='Unsubmitted search';
  w.tenantI18n.setLanguage('ru');await tick();
  assert.notEqual(section().parentElement,oldHost,'full header rerender occurred');
  assert.equal($('#operatorDialogueCompanyPicker'),panel,'same-route redraw preserves the open picker');
  assert.equal(panel.querySelector('input').value,'Unsubmitted search');
  assert.equal(calls.filter(c=>c.path.endsWith('/dialog-company')).length,readCount,'no duplicate company request while picker stays open');
  w.tenantI18n.setLanguage('en');await tick();
  await w.tenantOperator.openDialog(a);await tick();assert.equal(value(),'Company A');assert($('#operatorClientCard'),'existing Connect roster/name card retained');
  delaySearch=true;openSearch();panel=$('#operatorDialogueCompanyPicker');await tick();assert(releaseSearch);
  await source(1);assert.equal(value(),'Company B');assert(!search());
  delaySearch=false;releaseSearch();await tick();assert(!search(),'late search cannot resurrect old group source picker');
  assert(!search(),'read-only member has no binding control');
  delayRead=true;await source(0);assert(releaseRead);
  await source(1);assert.equal(value(),'Company B');releaseRead();await tick();
  assert.equal(value(),'Company B','late source A binding never bleeds into source B');
  bindings.set(41,{company_id:null,company_name:'Forbidden company name',can_manage:false});
  delayRead=false;await source(0);
  assert.equal(value(),'No company selected');
  assert(!section().outerHTML.includes('Forbidden company name'),'masked company name is absent from text, title and data attributes');
  assert(!search());
  assert.equal(calls.filter(c=>c.body&&c.path.endsWith('/dialog-company')).length,1);
  await w.tenantOperator.openDialog(operatorCandidate);await tick();
  const searchCalls=()=>calls.filter(c=>c.body?.operator_search);
  const writes=()=>calls.filter(c=>c.body?.operator_account);
  openSearch();
  const pop=()=>$('#operatorDialogueCompanySuggestions:not([hidden])');
  const remote=()=>pop()?.querySelector('[data-operator-account-results] button');
  const type=value=>{const input=search();input.value=value;input.dispatchEvent(new w.Event('input'));};
  assert(pop(),'default-off owner gets the local CRM picker');
  type('acct');await debounce();assert.equal(searchCalls().length,0);
  await w.tenantOperator.openDialog(b);await tick();
  bindings.set(45,{company_id:null,can_manage:true,can_search_operator:true});
  await w.tenantOperator.openDialog(operatorCandidate);await tick();
  openSearch();
  assert(pop()&&d.activeElement===search());
  assert.equal(searchCalls().length,0,'empty input only fetches a few local suggestions');
  bindings.set(45,{company_id:null,can_manage:true});
  d.dispatchEvent(new w.Event('tenant-dialog-reauthorized'));await tick();
  assert(!pop()&&search(),'reauthorization hides results when the Operator capability disappears');
  bindings.set(45,{company_id:null,can_manage:true,can_search_operator:true});
  d.dispatchEvent(new w.Event('tenant-dialog-reauthorized'));await tick();
  openSearch();
  type('abc');await debounce();assert.equal(searchCalls().length,0);
  type('acct');type(' acct_ ');await debounce();
  assert.equal(searchCalls().length,1);assert.equal(searchCalls()[0].body.operator_search,'acct_');
  assert.equal(searchCalls()[0].body.conversation_id,'');assert.equal(searchCalls()[0].body.channel_id,'');
  assert.deepEqual([...pop().querySelectorAll('[data-operator-account-results] strong')].map(n=>n.textContent),['@acct_001','@acct_002']);
  assert.equal(pop().querySelector('[data-local-company-results] button').textContent,'Can bind','local results share the search');
  assert(!pop().textContent.includes('Not contracted'));
  search().dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  assert.equal(writes().length,0,'typing, incoming results and Enter without a choice never bind');
  holdOperator=true;type('stale');await debounce();assert(releaseOperator);
  await w.tenantOperator.openDialog(b);await tick();assert(!pop());
  releaseOperator();holdOperator=false;await tick();assert(!pop(),'late search cannot repaint a superseded route');
  await w.tenantOperator.openDialog(operatorCandidate);await tick();
  openSearch();type('acct');await debounce();
  const pick=remote();pick.click();pick.click();
  await tick();await tick();
  assert.equal(writes().length,1);assert.equal(writes()[0].body.lead_id,'45');
  assert.equal(value(),'Bound Operator company');assert(!pop());
  openSearch();type('acct');await debounce();
  failedOperator=true;remote().click();await tick();
  assert.match(pop().textContent,/Check the current company link/);
  assert.equal(value(),'Bound Operator company','a rejected choice keeps the company');
  failedOperator=false;remote().click();await tick();
  assert.equal(writes().length,3,'the same displayed result can be retried');
  assert.equal(value(),'Bound Operator company');assert(!pop());
  assert(!calls.some(c=>c.path.includes('/send')));
  d.dispatchEvent(new w.CustomEvent('tenant-auth-pending'));assert(!section(),'auth transition immediately clears company');
  w.close();console.log('PASS: Company field and Operator chooser, exact Kommo/group routes, debounce, explicit bind, fallback, stale guards');
})().catch(err=>{console.error(err);process.exitCode=1;});
