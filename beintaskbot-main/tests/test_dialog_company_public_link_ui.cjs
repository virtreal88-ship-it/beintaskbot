const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
(async()=>{
  const dom=new JSDOM('<dialog><div id="host"></div></dialog><span id="name"></span>',
    {url:'https://crm.test/app',runScripts:'outside-only'});
  const w=dom.window,d=w.document,card=d.querySelector('dialog'),host=d.querySelector('#host');
  const URL='https://1000.az/AbC123',copied=[],calls=[],profiles=[];let clipboardFails=false,gestureWrites=0;
  w.tenantI18n={language:'az'};
  w.ClipboardItem=class{constructor(data){this.data=data;}};
  Object.defineProperty(w.navigator,'clipboard',{configurable:true,value:{writeText:async value=>{
    if(clipboardFails)throw Error('blocked');copied.push(value);
  },write(items){
    gestureWrites++;
    return items[0].data['text/plain'].then(blob=>new Promise((resolve,reject)=>{
      if(clipboardFails){reject(Error('blocked'));return;}
      const r=new w.FileReader();r.onload=()=>{copied.push(r.result);resolve();};r.onerror=reject;r.readAsText(blob);
    }));
  }}});
  w.fetch=(path,{signal,method,body,cache,headers})=>{
    assert.equal(method,'POST');assert.equal(cache,'no-store');assert.equal(headers['Content-Type'],'application/json');
    const data=JSON.parse(body);
    if(data.action==='company_profile'){
      assert.equal(path,'/api/platform/operator/dialog-company');
      return new Promise(resolve=>profiles.push(profile=>resolve({ok:true,json:async()=>({
        success:true,company_profile:profile})})));
    }
    assert.equal(path,'/api/platform/operator/dialog-company/public-link');
    assert.equal(data.action,'copy_customer_link');assert.equal('account' in data,false);assert.equal('public_base_url' in data,false);
    return new Promise(resolve=>calls.push({id:data.expected_company_id,channel:data.channel_id,signal,
      finish(link,status=200){resolve({ok:status===200,status,json:async()=>({success:status===200,public_link:link})});}}));
  };
  w.eval(fs.readFileSync('docs/tenant-dialog-company.js','utf8'));
  const api=w.tenantDialogueCompany,box=()=>host.querySelector('[data-company-public-link]'),
        icon=()=>box().querySelector('button'),press=()=>icon().click();
  function mount(company,channel='a'){
    api.mount({host,nameHost:d.querySelector('#name'),
      route:{provider:'connect',lead_id:42,channel_id:channel,conversation_id:'peer',
        company_binding:{company_id:company,company_name:'Synthetic'}},
      member:{tenant_id:'tenant',telegram_id:33},isCurrent:()=>true});
  }
  mount('first');assert.equal(calls.length,0);assert(box().hidden);
  card.open=true;api.refreshPublicLink();assert.equal(calls.length,0,'opening never issues a link');
  assert.equal(profiles.length,1);
  api.refreshPublicLink();assert.equal(profiles.length,1,'no duplicate read while the same panel remains open');
  assert.equal(box().textContent,'Müştəri linki');assert.equal(icon().getAttribute('aria-label'),'Linki kopyala');
  press();press();assert.equal(calls.length,1);assert.equal(gestureWrites,1);
  assert.equal(calls[0].id,'first');
  mount('second');assert(calls[0].signal.aborted);assert.equal(calls.length,1);
  profiles[0]({state:'available',registration_phone:'111111111',balance:123});await tick();
  assert(!host.querySelector('[data-company-profile]').textContent.includes('111111111'));
  profiles[1]({state:'available',registration_phone:'222222222',balance:0});await tick();
  assert(host.querySelector('[data-company-profile]').textContent.includes('222222222'));
  calls[0].finish({state:'available',url:'https://1000.az/Wrong1'});await tick();
  assert.equal(copied.length,0,'late first-company receipt cannot enter clipboard');
  press();clipboardFails=true;calls[1].finish({state:'available',url:URL});await tick();await tick();
  assert.match(box().textContent,/Brauzer linki kopyalamadı/);assert(!box().innerHTML.includes(URL));
  clipboardFails=false;press();calls[2].finish(undefined,403);await tick();
  assert.equal(copied.length,0,'retry uses current authorization');
  press();calls[3].finish({state:'available',url:URL});await tick();await tick();
  assert.deepEqual(copied,[URL]);
  mount('second','b');press();assert.equal(calls[4].channel,'b');
  api.hidePublicLink();card.open=false;calls[4].finish({state:'available',url:URL});await tick();
  assert(box().hidden,'late closed-panel result never copies');
  assert.deepEqual(copied,[URL]);
  card.open=true;api.refreshPublicLink();press();calls[5].finish(undefined,502);await tick();
  assert(!icon().disabled,'a read can be retried after an ambiguous failure');
  press();calls[6].finish({state:'available',url:URL});
  for(let i=0;i<6;i++)await tick();
  assert.deepEqual(copied,[URL,URL]);assert.match(box().textContent,/Link kopyalandı/);
  press();calls[7].finish({state:'available',url:URL});await tick();await tick();
  assert.deepEqual(copied,[URL,URL,URL]);assert.equal(calls.length,8,'sequential clicks verify the existing URL');
  mount('third');
  for(const [reply,status,message] of [
    [{state:'not_found'},200,/Operator mövcud linki qaytarmadı/],
    [{state:'not_connected'},200,/Operator qoşulmayıb/],
    [{state:'not_linked'},200,/təsdiqlənmiş Operator hesabı/],
    [undefined,424,/Operator link sorğusunu rədd etdi/],
    [undefined,403,/giriş icazəniz yoxdur/]
  ]){
    press();calls.at(-1).finish(reply,status);await tick();
    assert.match(box().textContent,message);
    assert.equal(copied.length,3,'failure does not change the clipboard');
  }
  Object.defineProperty(w.navigator,'clipboard',{value:undefined});
  press();await tick();assert.equal(calls.length,13,'unsupported clipboard does not make a request');
  assert.match(box().textContent,/kopyalama əlçatan deyil/);
  api.clear();assert.equal(box(),null);
  dom.window.close();
  console.log('dialog company one-click public-link assertions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
