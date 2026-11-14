/* Progress only: CRM lists still use their authorized snapshot endpoints. */
(() => {
  let profile, timer, generation = 0, watching = false;
  const boxes = {};
  const fullButtons = [];
  for (const [id, resource] of [['tasksRefresh','tasks'],['dealsRefresh','deals']]) {
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
  for (const [resource, view] of Object.entries({leads:'view-deals',tasks:'view-tasks'})) {
    const heading=document.querySelector('#'+view+' .top>div');
    if(!heading){boxes[resource]=[];continue;}
    const box=document.createElement('span');box.className='crm-sync-status';box.dataset.crmSyncStatus=resource;box.setAttribute('role','status');box.setAttribute('aria-live','polite');box.hidden=true;heading.append(box);boxes[resource]=[box];
  }
  function display(resource, text) {
    // Completed polling is not a warning banner. Active work and failures remain
    // visible beside the heading; the orange deal counter keeps its own meaning.
    const complete=text==='Kommo məlumatları yeniləndi.';
    for (const box of boxes[resource] || []) {
      box.textContent=complete?'':text;box.hidden=complete||!text;
      box.dataset.failed=String(!!text&&!complete&&!text.startsWith('Kommo fonda'));
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
  let syncPanel=null,syncStatus=null,syncSequence=0,syncResult=null,syncSaving=false;
  const syncWords={ru:{heading:'Синхронизация CRM',intro:'Настройте направление обмена контактами и сделками между Beintaskbot и CRM. По умолчанию синхронизация выключена. Старая история автоматически не импортируется.',contact:'Контакты',deal:'Сделки',provider:'CRM-система',direction:'Направление обмена',disabled:'Выключена',toExternal:'Beintaskbot → CRM',fromExternal:'CRM → Beintaskbot',both:'В обе стороны',save:'Сохранить правила синхронизации',loading:'Загружаем правила синхронизации…',ready:'Готово. Синхронизация начнётся только после включения направления.',saved:'Правила сохранены. Существующие записи CRM не изменены.',loadError:'Не удалось загрузить правила синхронизации.',saveError:'Не удалось сохранить правила синхронизации.',connection:'Подключение',queue:'В очереди',failed:'С ошибкой',conflicts:'Неразрешённые расхождения',empty:'нет',name:'Имя',phone:'Телефон',email:'Email',title:'Название сделки',pipeline_id:'Воронка продаж',stage_id:'Этап',contact_id:'Контакт',description:'Описание'},az:{heading:'CRM məlumatlarının sinxronizasiyası',intro:'Beintaskbot və CRM arasında kontakt və sövdələşmələrin mübadilə istiqamətini seçin. Standart olaraq sinxronizasiya söndürülüb. Köhnə tarixçə avtomatik idxal edilmir.',contact:'Əlaqələr',deal:'Sövdələşmələr',provider:'CRM sistemi',direction:'Məlumatın istiqaməti',disabled:'Söndürülüb',toExternal:'Beintaskbot → CRM',fromExternal:'CRM → Beintaskbot',both:'İki istiqamətli',save:'Sinxronizasiya qaydalarını saxla',loading:'Sinxronizasiya qaydaları yüklənir…',ready:'Hazırdır. Sinxronizasiya yalnız istiqamət aktiv ediləndə işləyir.',saved:'Qaydalar saxlanıldı. Mövcud CRM yazıları dəyişdirilmədi.',loadError:'Sinxronizasiya qaydalarını yükləmək olmadı.',saveError:'Sinxronizasiya qaydalarını saxlamaq olmadı.',connection:'Bağlantı',queue:'Növbə',failed:'Uğursuz',conflicts:'Həll olunmamış uyğunsuzluqlar',empty:'boş',name:'Ad',phone:'Telefon',email:'Email',title:'Sövdələşmənin adı',pipeline_id:'Satış vərəqi',stage_id:'Mərhələ',contact_id:'Əlaqə',description:'Təsvir'},en:{heading:'CRM data sync',intro:'Choose how contacts and deals move between Beintaskbot and CRM. Sync is off by default. Older history is not imported automatically.',contact:'Contacts',deal:'Deals',provider:'CRM system',direction:'Sync direction',disabled:'Off',toExternal:'Beintaskbot → CRM',fromExternal:'CRM → Beintaskbot',both:'Both directions',save:'Save sync rules',loading:'Loading sync rules…',ready:'Ready. Sync runs only after a direction is enabled.',saved:'Rules saved. Existing CRM records were not changed.',loadError:'Could not load sync rules.',saveError:'Could not save sync rules.',connection:'Connection',queue:'Queue',failed:'Failed',conflicts:'Unresolved conflicts',empty:'empty',name:'Name',phone:'Phone',email:'Email',title:'Deal title',pipeline_id:'Sales pipeline',stage_id:'Stage',contact_id:'Contact',description:'Description'}};
  const syncText=key=>(syncWords[window.tenantI18n?.language]||syncWords.az)[key]||key;
  const entityName=entity=>syncText(entity);
  const providerName=provider=>provider==='beinsystems'?'BeinSystems':'Kommo';
  const fieldNames={name:'name',phone:'phone',email:'email',title:'title',pipeline_id:'pipeline_id',stage_id:'stage_id',contact_id:'contact_id',description:'description'};
  const directions={off:'disabled',to_external:'toExternal',from_external:'fromExternal',bidirectional:'both'};
  const fieldOptions={contact:{kommo:['name','phone','email']},deal:{kommo:['title','pipeline_id','stage_id','contact_id'],beinsystems:['title','pipeline_id','stage_id','contact_id','description']}};
  function ensureSyncPanel(){
    const host=document.getElementById('tkCrmSyncHost');if(!host||profile?.role!=='owner')return false;
    if(syncPanel&&!syncPanel.isConnected){syncSequence++;syncSaving=false;syncResult=null;}
    syncPanel=document.getElementById('externalCrmSyncPanel');
    if(syncPanel&&!host.contains(syncPanel)){host.append(syncPanel);}
    if(!syncPanel){syncPanel=document.createElement('section');syncPanel.className='staff-section';syncPanel.id='externalCrmSyncPanel';syncPanel.innerHTML=`<h3>${syncText('heading')}</h3><p class="sub">${syncText('intro')}</p><div class="grid" id="externalCrmProviderStatus"></div><div id="externalCrmSyncSettings"></div><button class="outline" id="externalCrmSyncSave" type="button">${syncText('save')}</button><p class="sub" id="externalCrmSyncStatus" role="status" aria-live="polite"></p>`;host.append(syncPanel);syncStatus=syncPanel.querySelector('#externalCrmSyncStatus');syncPanel.querySelector('#externalCrmSyncSave').onclick=saveExternalSync;}
    syncStatus=syncPanel.querySelector('#externalCrmSyncStatus');
    return true;
  }
  function setSyncStatus(text){if(syncStatus)syncStatus.textContent=text;}
  function renderProviderStatus(result){
    const root=syncPanel?.querySelector('#externalCrmProviderStatus');if(!root)return;root.replaceChildren();
    const integrations=result.integrations||{},outbox=result.outbox||[],conflicts=result.conflicts||[];
    for(const provider of ['kommo','beinsystems']){
      const card=document.createElement('div');card.className='panel';
      const title=document.createElement('h4');title.textContent=providerName(provider);card.append(title);
      const status=document.createElement('p');status.className='sub';status.textContent=`${syncText('connection')}: ${integrations[provider]||syncText('empty')}`;card.append(status);
      const jobs=outbox.filter(item=>item.provider===provider),queued=jobs.reduce((n,item)=>n+Number(item.count||0),0),failed=jobs.filter(item=>item.state==='failed').reduce((n,item)=>n+Number(item.count||0),0);
      const queue=document.createElement('p');queue.className='sub';queue.textContent=`${syncText('queue')}: ${queued}; ${syncText('failed')}: ${failed}`;card.append(queue);
      const pending=conflicts.filter(item=>item.provider===provider).reduce((n,item)=>n+Number(item.count||0),0);const issue=document.createElement('p');issue.className='sub';issue.textContent=`${syncText('conflicts')}: ${pending}`;card.append(issue);root.append(card);
    }
  }
  function renderSync(data){
    const settingsRoot=syncPanel?.querySelector('#externalCrmSyncSettings');if(!settingsRoot)return;
    syncPanel.querySelector('h3').textContent=syncText('heading');syncPanel.querySelector('p.sub').textContent=syncText('intro');syncPanel.querySelector('#externalCrmSyncSave').textContent=syncText('save');
    const draft=Object.fromEntries([...settingsRoot.querySelectorAll('[data-entity]')].map(card=>[card.dataset.entity,{provider:card.querySelector('[data-provider]')?.value,direction:card.querySelector('[data-direction]')?.value,fields:[...card.querySelectorAll('[data-field]:checked')].map(input=>input.dataset.field)}]));
    syncResult=data.result;const savedRows=Object.fromEntries((data.result.settings||[]).map(row=>[row.entity_type,row]));
    renderProviderStatus(data.result);settingsRoot.replaceChildren();
    for(const entity of ['contact','deal']){
      const saved=savedRows[entity]||{},card=document.createElement('section');card.className='staff-section';card.dataset.entity=entity;
      const title=document.createElement('h4');title.textContent=entityName(entity);card.append(title);
      const providerLabel=document.createElement('label');providerLabel.append(document.createTextNode(syncText('provider')));const provider=document.createElement('select');provider.dataset.provider='';for(const id of ['kommo','beinsystems']){const option=document.createElement('option');option.value=id;option.textContent=providerName(id);provider.append(option)}providerLabel.append(provider);card.append(providerLabel);
      const directionLabel=document.createElement('label');directionLabel.append(document.createTextNode(syncText('direction')));const direction=document.createElement('select');direction.dataset.direction='';directionLabel.append(direction);card.append(directionLabel);
      const fields=document.createElement('div');fields.dataset.fields='';card.append(fields);
      const counts=document.createElement('p');counts.className='sub';counts.dataset.counts='';card.append(counts);
      const render=()=>{const chosen=provider.value,allowed=entity==='contact'?['off',...(chosen==='kommo'?['to_external','from_external','bidirectional']:[])]:['off','to_external',...(chosen==='kommo'?['from_external','bidirectional']:[])];direction.replaceChildren(...allowed.map(value=>{const option=document.createElement('option');option.value=value;option.textContent=syncText(directions[value]);return option}));fields.replaceChildren();for(const field of fieldOptions[entity][chosen]||[]){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.dataset.field=field;const selected=chosen===saved.provider&&(saved.allowed_fields||[]).includes(field),draftSelected=chosen===draft[entity]?.provider&&(draft[entity]?.fields||[]).includes(field);input.checked=draft[entity]?draftSelected:selected;label.append(input,document.createTextNode(' '+syncText(fieldNames[field])));fields.append(label)}direction.value=chosen===draft[entity]?.provider?draft[entity].direction:chosen===saved.provider?saved.sync_direction||'off':'off';counts.textContent=`${syncText('queue')}: ${(data.result.outbox||[]).filter(item=>item.entity_type===entity&&item.provider===chosen).reduce((n,item)=>n+Number(item.count||0),0)}; ${syncText('conflicts')}: ${(data.result.conflicts||[]).filter(item=>item.entity_type===entity&&item.provider===chosen).reduce((n,item)=>n+Number(item.count||0),0)}`;};
      provider.value=draft[entity]?.provider||saved.provider||'kommo';provider.onchange=()=>{fields.replaceChildren();render()};direction.onchange=()=>{if(direction.value==='off')return;};render();settingsRoot.append(card);
    }
  }
  async function loadExternalSync(){
    if(!ensureSyncPanel()||syncSaving)return;const current=++syncSequence,panel=syncPanel;setSyncStatus(syncText('loading'));
    try{const response=await fetch('/api/platform/crm/external-sync',{credentials:'same-origin',cache:'no-store'}),data=await response.json();if(current!==syncSequence||!panel.isConnected)return;if(!response.ok||!data.success)throw new Error(data.error||'Sinxronizasiya qaydaları alınmadı.');renderSync(data);setSyncStatus(syncText('ready'));}
    catch(error){if(current===syncSequence&&panel.isConnected)setSyncStatus(error.message||syncText('loadError'));}
  }
  async function saveExternalSync(){
    if(!syncPanel?.isConnected||profile?.role!=='owner'||syncSaving)return;const panel=syncPanel,current=syncSequence,valid=()=>current===syncSequence&&panel.isConnected&&syncPanel===panel;const button=panel.querySelector('#externalCrmSyncSave');syncSaving=true;panel.querySelectorAll('input,select,button').forEach(node=>node.disabled=true);setSyncStatus('Saxlanılır…');
    try{const settings=[...syncPanel.querySelectorAll('[data-entity]')].map(card=>{const entity=card.dataset.entity,provider=card.querySelector('[data-provider]').value,direction=card.querySelector('[data-direction]').value;return{entity_type:entity,provider,sync_direction:direction,mode:direction==='bidirectional'?'selective':direction==='from_external'?'external':'beintaskbot',primary_provider:direction==='to_external'?'beintaskbot':provider,allowed_fields:[...card.querySelectorAll('[data-field]:checked')].map(input=>input.dataset.field)}});const response=await fetch('/api/platform/crm/external-sync',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings})}),result=await response.json();if(!valid())return;if(!response.ok||!result.success)throw new Error(result.error||syncText('saveError'));setSyncStatus(syncText('saved'));syncSaving=false;panel.querySelectorAll('input,select,button').forEach(node=>node.disabled=false);await loadExternalSync();}
    catch(error){if(valid())setSyncStatus(error.message||syncText('saveError'));}finally{if(valid()){syncSaving=false;panel.querySelectorAll('input,select,button').forEach(node=>node.disabled=false);}}
  }
  document.addEventListener('tenant-profile',event=>{
    generation++;clearTimeout(timer);watching=false;const next=event.detail.member;const changed=profile?.tenant_id!==next?.tenant_id||profile?.telegram_id!==next?.telegram_id||profile?.role!==next?.role;profile=next;if(changed){syncSequence++;syncPanel=null;syncStatus=null;syncResult=null;syncSaving=false;}
    for(const button of fullButtons)button.hidden=!['owner','admin'].includes(profile?.role);
    for(const resource of Object.keys(boxes))display(resource,'');
    const modules=event.detail.capabilities?.modules||{};if(modules.tasks||modules.deals||modules.customers)start();
  });
  document.addEventListener('tenant-integration-panel-ready',event=>{if(event.detail?.provider==='kommo')queueMicrotask(()=>{if(profile?.role==='owner')void loadExternalSync();});});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);return;}if(watching){generation++;poll(generation);}});
  document.addEventListener('tenant-language',()=>{if(syncPanel?.isConnected&&!syncSaving&&profile?.role==='owner'&&syncResult)renderSync({result:syncResult});});
})();
