/* Progress only: CRM lists still use their authorized snapshot endpoints. */
(() => {
  let profile, timer, generation = 0, watching = false;
  const boxes = {};
  const fullButtons = [];
  for (const [id, resource] of [['tasksRefresh','tasks'],['dealsRefresh','deals'],['customersRefresh','deals']]) {
    const anchor = document.getElementById(id);
    if (!anchor) continue;
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'outline'; button.textContent = 'Tam yenilə'; button.hidden = true;
    anchor.after(button); fullButtons.push(button);
    button.onclick = async () => {
      const epoch = generation;
      button.disabled = true;
      try {
        const response = await fetch(`/api/platform/crm/${resource}?refresh=1&full=1`, {credentials:'same-origin'});
        const data = await response.json();
        if (epoch !== generation) return;
        if (!response.ok || !data.success || data.warning) throw new Error('full sync unavailable');
        start();
      } catch (_) {
        if (epoch === generation) display(resource === 'tasks' ? 'tasks':'leads', 'Tam sinxronizasiya başlamadı. Kommo bağlantısını yoxlayın.');
      } finally { button.disabled = false; }
    };
  }
  for (const [resource, ids] of Object.entries({leads:['dealsNotice','customersNotice'], tasks:['tasksNotice']})) {
    boxes[resource] = ids.map(id => {
      const anchor = document.getElementById(id);
      if (!anchor) return null;
      const box = document.createElement('div');
      box.className = 'notice'; box.setAttribute('role', 'status');
      anchor.after(box); return box;
    }).filter(Boolean);
  }
  function display(resource, text) {
    for (const box of boxes[resource] || []) {
      box.textContent = text; box.classList.toggle('show', !!text);
    }
  }
  function schedule(epoch) {
    clearTimeout(timer);
    if (epoch === generation && watching && !document.hidden) timer = setTimeout(() => poll(epoch), 5000);
  }
  async function poll(epoch) {
    if (!profile || epoch !== generation || document.hidden) return;
    try {
      const response = await fetch('/api/platform/crm/sync', {credentials:'same-origin'});
      const data = await response.json();
      if (epoch !== generation) return;
      if (!response.ok || !data.success) throw new Error('status unavailable');
      let active = false;
      const completed = [];
      for (const job of data.jobs || []) {
        if (job.status === 'queued' || job.status === 'running') {
          active = true;
          display(job.resource, `Kommo fonda yenilənir… ${job.pages_done || 0} səhifə yükləndi. Saxlanmış siyahı göstərilir.`);
        } else if (job.status === 'failed') {
          display(job.resource, 'Sinxronizasiya dayandı. Kommo bağlantısını yoxlayın və Yenilə düyməsini basın.');
        } else if (job.status === 'done') {
          display(job.resource, 'Kommo məlumatları yeniləndi.');
          completed.push(job.resource);
        }
      }
      watching = active;
      if (!active && completed.length) document.dispatchEvent(new CustomEvent('tenant-crm-sync-complete', {
        detail: {resources:completed, tenant_id:profile.tenant_id}
      }));
    } catch (_) {
      if (epoch !== generation) return;
      watching = false;
      for (const resource of Object.keys(boxes)) display(resource, 'Sinxronizasiya vəziyyəti alınmadı. Yenilə düyməsi ilə yoxlayın.');
    }
    schedule(epoch);
  }
  function start() {
    if (!profile) return;
    generation++; watching = true; clearTimeout(timer); poll(generation);
  }
  window.tenantCrmSyncWatch = start;
  document.addEventListener('tenant-profile', event => {
    generation++; clearTimeout(timer); watching = false;
    profile = event.detail.member;
    for (const button of fullButtons) button.hidden = !['owner','admin'].includes(profile?.role);
    for (const resource of Object.keys(boxes)) display(resource, '');
    const modules = event.detail.capabilities?.modules || {};
    if (modules.tasks || modules.deals || modules.customers) start();
    if(syncPanel){syncPanel.hidden=profile?.role!=='owner';if(profile?.role==='owner')loadExternalSync()}
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(timer); return; }
    if (watching) { generation++; poll(generation); }
  });
  const syncPanel=document.getElementById('externalCrmSyncPanel');
  const syncStatus=document.getElementById('externalCrmSyncStatus');
  const syncText=key=>window.tenantI18n?.t(key)||key;
  function setSyncStatus(key){if(!syncStatus)return;syncStatus.dataset.i18n=key;syncStatus.textContent=syncText(key)}
  document.addEventListener('tenant-language',()=>{if(syncStatus?.dataset.i18n)setSyncStatus(syncStatus.dataset.i18n)});
  const fieldOptions={contact:{kommo:['name','phone','email']},deal:{kommo:['title','pipeline_id','stage_id','contact_id'],beinsystems:['title','pipeline_id','stage_id','contact_id','description']}};
  function renderProviderStatus(result){
    const root=document.getElementById('externalCrmProviderStatus');if(!root)return;
    const integrations=result.integrations||{},caps=result.capabilities||{},outbox=result.outbox||[],conflicts=result.conflicts||[];
    root.replaceChildren();
    for(const [provider,label] of [['kommo','Kommo'],['beinsystems','BeinSystems']]){
      const card=document.createElement('article');card.className='panel';
      const title=document.createElement('h3');title.textContent=label;card.append(title);
      const status=document.createElement('p');status.className='sub';status.textContent=`Bağlantı: ${integrations[provider]||'məlumat əlçatan deyil'}`;card.append(status);
      const supported=[];for(const [entity,names] of Object.entries(caps[provider]||{})){if(names.length)supported.push(`${entity==='contact'?'Əlaqələr':'Sövdələşmələr'}: ${names.map(x=>({to_external:'Beintaskbot → CRM',from_external:'CRM → Beintaskbot',bidirectional:'iki istiqamətli'}[x]||x)).join(', ')}`);else supported.push(`${entity==='contact'?'Əlaqələr':'Sövdələşmələr'}: dəstəklənmir`)}
      const capability=document.createElement('p');capability.className='sub';capability.textContent=supported.join(' · ');card.append(capability);
      const queue=document.createElement('p');queue.className='sub';const rows=outbox.filter(x=>x.provider===provider);queue.textContent=`Növbə: ${rows.length?rows.map(x=>`${x.entity_type} ${x.state}: ${x.count}`).join(' · '):'boş'}; failed: ${rows.filter(x=>x.state==='failed').reduce((n,x)=>n+Number(x.count||0),0)}`;card.append(queue);
      const pending=conflicts.filter(x=>x.provider===provider).reduce((n,x)=>n+Number(x.count||0),0);const errors=document.createElement('p');errors.className='sub';errors.textContent=`Həll edilməmiş münaqişələr: ${pending}; fərdi xəta detalları API-dən əlçatan deyil.`;card.append(errors);root.append(card);
    }
  }
  async function loadExternalSync(){
    if(!syncPanel||profile?.role!=='owner')return;
    try{
      const res=await fetch('/api/platform/crm/external-sync',{credentials:'same-origin'}),data=await res.json();
      if(!res.ok||!data.success)throw new Error();
      const rows=Object.fromEntries((data.result.settings||[]).map(x=>[x.entity_type,x]));
      renderProviderStatus(data.result);
      const root=document.getElementById('externalCrmSyncSettings');root.replaceChildren();
      for(const entity of ['contact','deal']){
        const saved=rows[entity]||{},card=document.createElement('div');card.className='grid';card.dataset.entity=entity;
        card.innerHTML=`<b>${entity==='contact'?'Əlaqələr':'Sövdələşmələr'}</b><label>Provider<select data-provider><option value="kommo">Kommo</option><option value="beinsystems">BeinSystems</option></select></label><label>İstiqamət<select data-direction></select></label><div data-fields></div><div class="sub" data-counts></div>`;
        const provider=card.querySelector('[data-provider]'),direction=card.querySelector('[data-direction]');
        const render=()=>{const p=provider.value,allowed=entity==='contact'?['off',...(p==='kommo'?['to_external','from_external','bidirectional']:[])]:['off','to_external',...(p==='kommo'?['from_external','bidirectional']:[])];direction.replaceChildren(...allowed.map(v=>{const o=document.createElement('option');o.value=v;o.textContent=({off:'Söndürülüb',to_external:'Beintaskbot → CRM',from_external:'CRM → Beintaskbot',bidirectional:'İki istiqamətli'})[v];return o}));const fieldBox=card.querySelector('[data-fields]');fieldBox.replaceChildren();for(const f of fieldOptions[entity][p]||[]){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.dataset.field=f;input.checked=(saved.allowed_fields||[]).includes(f);label.append(input,document.createTextNode(' '+f));fieldBox.append(label)} };
        provider.value=saved.provider||'kommo';render();direction.value=saved.sync_direction||'off';provider.onchange=()=>{saved.allowed_fields=[];render();direction.value='off'};
        const counts=(data.result.outbox||[]).filter(x=>x.entity_type===entity&&x.provider===provider.value).map(x=>`${x.state}: ${x.count}`).join(' · ');const conflicts=(data.result.conflicts||[]).filter(x=>x.entity_type===entity&&x.provider===provider.value).reduce((n,x)=>n+Number(x.count||0),0);card.querySelector('[data-counts]').textContent=`Növbə: ${counts||'boş'}; həll edilməmiş münaqişə: ${conflicts}; bağlantı: ${(data.result.integrations||{})[provider.value]||'yoxlanılmayıb'}`;
        root.append(card);
      }
      setSyncStatus('crmSyncLoaded');
    }catch(_){setSyncStatus('crmSyncLoadError')}
  }
  document.getElementById('externalCrmSyncSave')?.addEventListener('click',async()=>{
    const status=syncStatus;status.dataset.i18n='crmSyncSaving';status.textContent=syncText('crmSyncSaving');
    try{const settings=[...document.querySelectorAll('#externalCrmSyncSettings [data-entity]')].map(card=>{const entity=card.dataset.entity,provider=card.querySelector('[data-provider]').value,direction=card.querySelector('[data-direction]').value;return{entity_type:entity,provider,sync_direction:direction,mode:direction==='bidirectional'?'selective':direction==='from_external'?'external':'beintaskbot',primary_provider:direction==='to_external'?'beintaskbot':provider,allowed_fields:[...card.querySelectorAll('[data-field]:checked')].map(x=>x.dataset.field)}});const res=await fetch('/api/platform/crm/external-sync',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings})}),data=await res.json();if(!res.ok||!data.success)throw new Error(data.error||'Yoxlama uğursuz oldu.');status.dataset.i18n='crmSyncSaved';status.textContent=syncText('crmSyncSaved');await loadExternalSync()}catch(e){status.dataset.i18n='';status.textContent=e.message||syncText('crmSyncSaveError')}
  });
})();
