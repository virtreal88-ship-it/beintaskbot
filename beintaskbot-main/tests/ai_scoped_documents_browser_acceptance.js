/* Executed by Chromium against the full, unmodified production AI controller.
   The server declares whether API authority is synthetic or real cookie/PG. */
(async()=>{
 const result=document.createElement('pre');result.id='aiAcceptanceResult';result.dataset.result='running';document.body.append(result);
 const config=window.AI_ACCEPTANCE_CONFIG,events=[];
 const assert=(ok,message)=>{if(!ok)throw new Error(message);};
 const wait=async(check,message)=>{for(let i=0;i<300;i++){if(await check())return;await new Promise(resolve=>setTimeout(resolve,20));}throw new Error('Timeout: '+message);};
 const host=()=>document.getElementById('aiScopedDocuments');
 const control=async(command)=>{const r=await fetch('/test/'+command,{cache:'no-store'});assert(r.ok,'test control '+command);return r.json();};
 const profile=async()=>{const r=await fetch('/api/platform/me',{cache:'no-store'});const data=await r.json();window.tenantProfileSnapshot=data;document.dispatchEvent(new CustomEvent('tenant-profile',{detail:data}));return data;};
 const mode=async(name)=>{await control('mode?name='+name);await profile();};
 const read=()=>host()?.querySelector('[data-scoped-list]');
 const ownCard=()=>[...host().querySelectorAll('[data-scoped-list] article')].find(node=>node.querySelector('h3')?.textContent.startsWith('Own agent'));
 const form=()=>host().querySelector('form');
 const edit=()=>ownCard()?.querySelector('button')?.click();
 const submit=()=>form().querySelector('[type=submit]').click();
 const direct=async(path,method,data)=>{const r=await fetch('/api/platform/ai/'+path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify({...data,expected_tenant_id:config.tenant,expected_user_id:config.actor})});return {status:r.status,body:await r.json()};};
 try{
  await wait(()=>read()?.textContent.includes('Own agent'),'initial R-only list');
  assert(!host().querySelector('[data-scoped-create]'),'R-only create absent');
  assert(![...host().querySelectorAll('button')].some(button=>button.textContent==='Edit'),'R-only update absent');
  let denied=await direct('agents/'+config.own,'PATCH',{name:'ILLEGAL READ WRITE',expected_version:1});assert([403,404].includes(denied.status),'R-only backend rejects save');
  const readBefore=(await control('state')).list_reads;
  await mode('create');await wait(()=>host().querySelector('[data-scoped-create]')&&!host().querySelector('[data-scoped-create]').disabled,'C-only ready create button');
  await wait(()=>host().querySelector('[data-scoped-list]')?.textContent.includes('not allowed'),'C-only settled');
  host().querySelector('[data-scoped-create]').click();
  await wait(()=>form()?.elements.sources.options.length===1,'source catalog only assigned source');
  assert(form().elements.enabled.disabled,'Create cannot activate');
  form().elements.name.value='Created through Chromium';form().elements.instructions.value='Known submitted instructions';
  form().elements.sources.options[0].selected=true;submit();
  await wait(()=>host().querySelector('[data-scoped-notice]')?.textContent==='Saved','create saved');
  let state=await control('state');assert(state.list_reads===readBefore,'C-only never GETs agent list');
  assert(state.created===1,'exactly one created row');assert(JSON.stringify(state.receipts.at(-1).sort())==='["id","version"]','minimal create receipt');
  denied=await direct('agents','POST',{name:'FORBIDDEN SOURCE',instructions:'x',scopes:[{provider:'connect',channel_id:config.otherSource}]});assert(denied.status===403,'unassigned source rejected');
  assert((await control('state')).created===1,'source denial made no row');
  await mode('mixed');await wait(()=>ownCard()?.querySelector('button'),'per-row U loaded');
  const peer=[...read().querySelectorAll('article')].find(node=>node.querySelector('h3').textContent==='Peer agent');assert(peer&&!peer.querySelector('button'),'peer R does not imply U');
  edit();host().querySelector('[data-scoped-cancel]').click();assert(!form(),'Cancel closes editor');
  edit();form().elements.name.value='Own agent changed';submit();
  await wait(()=>read()?.textContent.includes('Own agent changed')&&!form(),'update readback');
  state=await control('state');assert(state.own_name==='Own agent changed','actual store changed');assert(JSON.stringify(state.receipts.at(-1).sort())==='["id","version"]','minimal update receipt');
  edit();form().elements.name.value='STALE REVOKED WRITE';await control('revoke');submit();
  await wait(()=>!host().querySelector('form')&&!host().textContent.includes('Own agent changed'),'revocation clears private DOM');
  assert((await control('state')).own_name==='Own agent changed','revoked mutation cannot write');
  await mode('mixed');await wait(()=>ownCard()?.querySelector('button'),'restore scope');
  await control('hold');host().querySelector('[data-scoped-refresh]').click();
  await wait(async()=>(await control('state')).pending===1,'old authorized list pending');
  await mode('foreign');await control('release');await wait(async()=>(await control('state')).released===1,'old response released');
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  assert(!host().textContent.includes('Own agent')&&!host().textContent.includes('Created through Chromium'),'stale identity data discarded');
  state=await control('state');assert(state.shared.length===0,'no shared API dependencies: '+state.shared.join(','));
  assert(window.AI_ACCEPTANCE_ERRORS.length===0,'production controller errors: '+window.AI_ACCEPTANCE_ERRORS.join(';'));
  const box=host().getBoundingClientRect();assert(box.x>=-1&&box.right<=innerWidth+1,'scoped console fits viewport');
  result.dataset.result='passed';result.textContent='PASS '+config.backend+' production AI controller: independent C/R/U, source denial, receipts, revocation, stale identity, no shared APIs; viewport='+innerWidth;
 }catch(error){result.dataset.result='failed';result.textContent=error.stack||error.message;}
})();
