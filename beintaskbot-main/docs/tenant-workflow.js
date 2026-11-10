/* Owner workflow editor. All choices and rights belong to the current tenant. */
(() => {
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const roles = {owner:'Şirkət sahibi',admin:'Administrator',manager:'Vərəq istifadəçisi',worker:'İcraçı',master:'Usta'};
  const modules = {deals:'Sövdələşmələr',tasks:'Tapşırıqlar',customers:'Müştərilər',hot_orders:'İsti sifarişlər',finance:'Maliyyə',linear:'Linear',settings:'Tənzimləmələr',employees:'Əməkdaşlar',integrations:'İnteqrasiyalar'};
  let session, config, catalog = {pipelines:[],users:[]}, loaded = false, loading = false;
  async function api(path, data) {
    const response = await fetch(path, {credentials:'same-origin',cache:'no-store',
      ...(data ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)} : {})});
    const result = await response.json();
    if(!response.ok || !result.success) throw new Error(result.error || 'Ayarlar yüklənmədi.');
    return result;
  }
  function policies() {
    return Object.fromEntries((config.policies || []).map(row => [row.policy_key,row.value]));
  }
  function check(name, label, on) {
    return `<label class="wf-check"><input type="checkbox" data-wf="${escape(name)}" ${on ? 'checked' : ''}>${escape(label)}</label>`;
  }
  function install(data) {
    session = data;
    if(data.member.role !== 'owner' || $('workflowPanel')) return;
    document.head.insertAdjacentHTML('beforeend', `<style>
      .wf-wide{grid-column:1/-1}.wf-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}
      .wf-tabs button[aria-selected=true]{background:#e6f6ed;border-color:#73b59e}
      .wf-pane[hidden]{display:none}.wf-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}
      .panel .wf-check{display:flex;align-items:center;gap:8px;margin:8px 0;font-weight:500}
      .panel .wf-check input{width:auto;flex-shrink:0}.wf-card{border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:12px}
      .wf-card summary{cursor:pointer;font-weight:650}.wf-card h3{margin:0 0 10px}.wf-hint{font-size:12px;color:var(--muted)}
      .wf-footer{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:16px}.wf-result{font-size:13px;white-space:pre-wrap}
      .wf-notification-row{display:grid;grid-template-columns:minmax(0,1fr) 70px 50px;gap:8px;align-items:center;border-bottom:1px solid var(--line);padding:7px 0;font-size:13px}
      .wf-notification-row[hidden]{display:none}.panel .wf-notification-row label{display:flex;justify-content:center;margin:0}.panel .wf-notification-row input{width:auto}
    </style>`);
    document.querySelector('#view-settings .grid').insertAdjacentHTML('beforeend', `<article class="panel owner-only wf-wide" id="workflowPanel">
      <h2>İş qaydaları</h2><p>Vərəqləri və şirkət qaydalarını buradan idarə edin.</p>
      <button class="outline" id="wfLoad">Ayarları aç</button>
      <div id="wfEditor" hidden><div class="wf-tabs" role="tablist">
        <button class="outline" data-wf-tab="pipelines" role="tab" aria-selected="true">Vərəqlər və mərhələlər</button>
        <button class="outline" data-wf-tab="members" role="tab" aria-selected="false">Vərəqlərə giriş</button>
        <button class="outline" data-wf-tab="rules" role="tab" aria-selected="false">İş qaydaları</button>
      </div><div id="wf-pipelines" class="wf-pane"></div><div id="wf-members" class="wf-pane" hidden></div><div id="wf-rules" class="wf-pane" hidden></div><button class="outline" id="wfAddLocalPipeline" type="button">+ Beintaskbot vərəqi yarat</button>
      <div class="wf-footer"><button class="primary" id="wfSave">Ayarları saxla</button><span class="wf-hint">Dəyişikliklər növbəti sorğudan etibarən tətbiq olunur.</span></div></div>
      <div id="wfResult" class="wf-result" role="status" aria-live="polite"></div></article>`);
    const invite = $('inviteRole');
    if(invite && ![...invite.options].some(option => option.value === 'admin')) invite.insertAdjacentHTML('beforeend','<option value="admin">Administrator</option>');
    $('wfLoad').onclick = load;
    $('wfSave').onclick = save;
    $('wfAddLocalPipeline').onclick = addLocalPipeline;
    $('workflowPanel').querySelectorAll('[data-wf-tab]').forEach(button => button.onclick = () => {
      $('workflowPanel').querySelectorAll('[data-wf-tab]').forEach(tab => tab.setAttribute('aria-selected',String(tab === button)));
      $('workflowPanel').querySelectorAll('.wf-pane').forEach(pane => {pane.hidden = pane.id !== 'wf-'+button.dataset.wfTab;});
      $('wfAddLocalPipeline').hidden=button.dataset.wfTab!=='pipelines';
    });
  }
  function combinedPipelines() {
    const saved = new Map((config.pipelines || []).map(row => [String(row.pipeline_id),row]));
    const selected = new Map((((session.member.onboarding || {}).pipeline || {}).selected || []).map(row => [String(row.id),row]));
    const all = new Map(catalog.pipelines.map(row => [String(row.pipeline_id),row]));
    for(const row of config.pipelines || []) if(!all.has(String(row.pipeline_id))) all.set(String(row.pipeline_id), {...row,stages:[]});
    return [...all.values()].map(row => {
      const prior = saved.get(String(row.pipeline_id));
      const stageMap = new Map((row.stages || []).map(stage => [String(stage.stage_id), stage]));
      for(const stage of config.stages || []) if(String(stage.pipeline_id) === String(row.pipeline_id)) stageMap.set(String(stage.stage_id),stage);
      return {...row, ...prior, active:prior ? prior.active : (row.native ? true : selected.has(String(row.pipeline_id))),
        stages:[...stageMap.values()].map(stage => ({...stage, settings:{...(stage.settings || {}), visible: stage.settings?.visible ?? (!selected.get(String(row.pipeline_id))?.stage_ids?.length || selected.get(String(row.pipeline_id)).stage_ids.includes(Number(stage.stage_id)))}}))};
    });
  }
  async function load() {
    if(loading) return;
    loading=true; $('wfLoad').disabled=true; $('wfLoad').textContent='Yüklənir…'; $('wfResult').textContent='';
    try {
      const [me, workflow, choices] = await Promise.all([api('/api/platform/me'),api('/api/platform/workflow'),api('/api/platform/workflow/catalog')]);
      session=me; config=workflow; catalog=choices;
      render(); loaded=true; $('wfEditor').hidden=false; $('wfLoad').textContent='Kommo siyahısını yenilə';
      $('wfResult').textContent=(choices.warnings || []).join('\n');
    } catch(error) { $('wfResult').textContent=error.message; $('wfLoad').textContent='Yenidən yoxla'; }
    finally {loading=false; $('wfLoad').disabled=false;}
  }
  function render() {
    const rows=combinedPipelines(), rules=policies(), members=(session.members || []).filter(member => member.active);
    const memberOptions=members.map(member => `<option value="${escape(member.telegram_id)}">${escape(member.display_name)}</option>`).join('');
    const taskMemberOptions=members.filter(member=>member.role==='owner'||(member.permissions||[]).includes('tasks')).map(member=>`<option value="${escape(member.telegram_id)}">${escape(member.display_name)}</option>`).join('');
    $('wf-pipelines').innerHTML=rows.map(row => `<details class="wf-card" data-pipeline="${escape(row.pipeline_id)}">
      <summary>${escape(row.name)}</summary>${check('active','Bu vərəq aktivdir',row.active)}
      <label>Vərəqin sahibi<select data-owner><option value="">Təyin edilməyib</option>${memberOptions}</select></label>
      <p class="wf-hint">Bu tenant vərəqi Beintaskbot-da saxlanılır; Kommo dəyişdirilmir.</p><button class="outline" type="button" data-add-stage="${escape(row.pipeline_id)}">+ Mərhələ</button>
      ${(row.stages || []).map(stage => `<div class="wf-grid wf-card" data-stage="${escape(stage.stage_id)}">
        <div>${check('visible',stage.name,stage.settings?.visible !== false)}</div>
        <label>Sıra<input type="number" data-sort value="${Number(stage.sort_order) || 0}" min="0"></label>
        <label>Növ<select data-stage-type><option value="open">İşdə</option><option value="won">Uğurlu</option><option value="lost">İmtina</option></select></label>
        <label class="wf-wide"><input type="checkbox" data-native-task-enabled ${stage.settings?.native_task?.enabled ? 'checked' : ''}> Mərhələyə daxil olduqda Beintaskbot tapşırığı yarat</label>
        <label class="wf-wide">Tapşırıq mətni<input data-native-task-text maxlength="3500" value="${escape(stage.settings?.native_task?.text || '')}" placeholder="Müştəri ilə əlaqə saxla"></label>
        <label>İcraçı<select data-native-task-responsible><option value="">Sövdələşmənin məsulu</option>${taskMemberOptions}</select></label>
        <label>Son tarix (gün)<input type="number" data-native-task-days min="0" max="365" value="${Number(stage.settings?.native_task?.due_days ?? 1)}"></label></div>`).join('')}</details>`).join('') || '<p>Beintaskbot vərəqi yaradın; Kommo bağlantısı tələb olunmur.</p>';
    rows.forEach(row => {
      const element=$('wf-pipelines').querySelector(`[data-pipeline="${CSS.escape(String(row.pipeline_id))}"]`);
      element.querySelector('[data-owner]').value=String(row.owner_telegram_id || '');
      (row.stages || []).forEach(stage => {element.querySelector(`[data-stage="${CSS.escape(String(stage.stage_id))}"] [data-stage-type]`).value=stage.stage_type || 'open';});
    });
    $('wf-pipelines').querySelectorAll('[data-add-stage]').forEach(button=>button.onclick=()=>{const pipeline=rows.find(row=>String(row.pipeline_id)===button.dataset.addStage);if(!pipeline)return;pipeline.stages.push({stage_id:String(nextLocalId()),name:'Yeni mərhələ',sort_order:pipeline.stages.length,stage_type:'open',settings:{visible:true}});catalog.pipelines=[...catalog.pipelines.filter(row=>String(row.pipeline_id)!==String(pipeline.pipeline_id)),pipeline];render();});
    $('wf-members').innerHTML='<p>Əməkdaşın rolu, hüquqları və iş ayarları: Şirkət → Əməkdaşlar.</p>'+members.filter(member=>member.role === 'manager').map(member => {
      const settings=(rules.members || {})[String(member.telegram_id)] || {};
      const selected=(settings.pipeline_ids || []).map(String);
      const options=[...rows, ...selected.filter(id=>!rows.some(row=>String(row.pipeline_id)===id)).map(id=>({pipeline_id:id,name:id}))];
      return `<details class="wf-card" data-member="${escape(member.telegram_id)}"><summary>${escape(member.display_name)} · ${escape(roles[member.role])}</summary>
        <label>İcazəli vərəqlər<select multiple data-member-pipelines>${options.map(row => `<option value="${escape(row.pipeline_id)}" ${selected.includes(String(row.pipeline_id)) ? 'selected' : ''}>${escape(row.name)}</option>`).join('')}</select></label></details>`;
    }).join('');
    const defaults={...session.member.modules,...rules.modules};
    $('wf-rules').innerHTML=`<h3>Modullar</h3><div class="wf-grid" data-wf-modules>${Object.entries(modules).filter(([key])=>!['settings','employees','integrations'].includes(key)).map(([key,label])=>check(key,label,defaults[key])).join('')}</div>
      <h3>Tapşırıq təsdiqləri</h3><div data-task-rules>${check('creation_requires_admin','Yaradılarkən təsdiq tələb olunur',rules.task_approval?.creation_requires_admin)}${check('completion_requires_admin','Tamamlanarkən təsdiq tələb olunur',rules.task_approval?.completion_requires_admin)}${check('self_created_exempt','Özünə yaradılmış tapşırıq təsdiq tələb etmir',rules.task_approval?.self_created_exempt !== false)}</div>
      <h3>Sövdələşmələr</h3><div data-deal-rules>${check('requires_admin','Tamamlanarkən təsdiq tələb olunur',rules.deal_completion?.requires_admin)}</div>
      <div data-hot-rules>${window.tenantHotOrderSettings.render(rules.hot_orders || {})}</div>
      <h3>Maliyyə</h3><p class="wf-hint">Valyuta: AZN. Əməliyyatlar real bank köçürməsi deyil. Köhnə balanslar dəyişmir.</p><div data-finance-rules>${check('allow_negative_balances','Mənfi balansa icazə ver',rules.finance?.allow_negative_balances===true)}</div>
      <h3>Şirkətin bildiriş məhdudiyyətləri</h3><div data-notifications>${Object.entries({...Object.fromEntries((session.capabilities?.notification_events || []).map(event=>[event.key,true])),telegram:true,push:true,...session.member.notification_rules,...rules.notifications}).map(([key,on])=>check(key,({telegram:'Telegram kanalı',push:'Push kanalı'})[key] || (session.capabilities?.notification_events || []).find(event=>event.key === key)?.label || key,on)).join('')}</div>
      <p class="wf-hint">SaaS: tapşırıq təyini, gecikmə, yeni sövdələşmə və daxil olan mesajlar üçün Telegram/Push qoşulub. Hər əməkdaş üçün hadisə və kanalı ayrıca seçin. Təsdiqin nəticəsi yalnız sorğunu yaradan əməkdaşa gəlir. Köhnə məlumatlar toplu göndərilmir.</p>`;
    window.tenantHotOrderSettings.install($('wf-rules').querySelector('[data-hot-rules]'));
  }
  let localIdCounter=0;
  function nextLocalId(){localIdCounter++;return Math.floor(1000000000+Math.random()*8000000000000000);}
  function addLocalPipeline(){const name=prompt('Yeni Beintaskbot vərəqinin adı:');if(!name||!name.trim())return;const id=String(nextLocalId()),stage={stage_id:String(nextLocalId()),name:'Yeni',sort_order:0,stage_type:'open',settings:{visible:true}};catalog.pipelines.push({pipeline_id:id,name:name.trim().slice(0,180),active:true,owner_telegram_id:null,stages:[stage],settings:{provider:'beintaskbot'},native:true});render();}
  const flags = root => Object.fromEntries([...root.querySelectorAll('input[data-wf]')].map(input => [input.dataset.wf,input.checked]));
  async function save() {
    if(!loaded || loading) return;
    loading=true; $('wfSave').disabled=true; $('wfSave').textContent='Saxlanılır…'; $('wfResult').textContent='';
    try {
      const rows=combinedPipelines(), nextRules={...policies()}, nextPipelines=[], nextStages=[], memberPipelineChanges=[];
      $('wf-pipelines').querySelectorAll('[data-pipeline]').forEach(card => {
        const row=rows.find(p=>String(p.pipeline_id)===card.dataset.pipeline);
        nextPipelines.push({...row,stages:undefined,active:card.querySelector('[data-wf="active"]').checked,owner_telegram_id:Number(card.querySelector('[data-owner]').value) || null});
        card.querySelectorAll('[data-stage]').forEach(stage => {
          const original=row.stages.find(s=>String(s.stage_id)===stage.dataset.stage);
          nextStages.push({...original,pipeline_id:row.pipeline_id,sort_order:Number(stage.querySelector('[data-sort]').value),stage_type:stage.querySelector('[data-stage-type]').value,settings:{...original.settings,visible:stage.querySelector('[data-wf="visible"]').checked,native_task:{enabled:stage.querySelector('[data-native-task-enabled]').checked,text:stage.querySelector('[data-native-task-text]').value.trim(),responsible_id:Number(stage.querySelector('[data-native-task-responsible]').value)||null,due_days:Number(stage.querySelector('[data-native-task-days]').value||1)}}});
        });
      });
      $('wf-members').querySelectorAll('[data-member]').forEach(card => {
        const prior=(nextRules.members || {})[card.dataset.member] || {};
        const selected=[...card.querySelector('[data-member-pipelines]').selectedOptions].map(option=>option.value);
        const oldIds=(prior.pipeline_ids || []).map(String);
        if(selected.length !== oldIds.length || selected.some(id=>!oldIds.includes(id))) {
          memberPipelineChanges.push({telegram_id:card.dataset.member,expected_pipeline_ids:prior.pipeline_ids ?? null,pipeline_ids:selected});
        }
      });
      delete nextRules.members;
      nextRules.modules=flags($('wf-rules').querySelector('[data-wf-modules]'));
      nextRules.task_approval=flags($('wf-rules').querySelector('[data-task-rules]'));
      nextRules.deal_completion=flags($('wf-rules').querySelector('[data-deal-rules]'));
      nextRules.hot_orders=window.tenantHotOrderSettings.read($('wf-rules').querySelector('[data-hot-rules]'),nextRules.hot_orders || {});
      nextRules.finance={...(nextRules.finance || {}),currency:'AZN',...flags($('wf-rules').querySelector('[data-finance-rules]'))};
      nextRules.notifications=flags($('wf-rules').querySelector('[data-notifications]'));
      config=await api('/api/platform/workflow',{pipelines:nextPipelines,stages:nextStages,policies:nextRules,member_pipeline_changes:memberPipelineChanges});
      session=await api('/api/platform/me');
      render();
      document.dispatchEvent(new CustomEvent('tenant-profile',{detail:session}));
      $('wfResult').textContent='Ayarlar saxlanıldı.';
    } catch(error) { $('wfResult').textContent='Saxlama tamamlanmadı: '+error.message; }
    finally {loading=false;$('wfSave').disabled=false;$('wfSave').textContent='Ayarları saxla';}
  }
  document.addEventListener('tenant-profile',event=>install(event.detail));
  // The editor may arrive after the app's first fast cached session response.
  api('/api/platform/me').then(install).catch(()=>{});
})();
