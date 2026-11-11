/* Tenant configuration only: no issues written and no news published here. */
(() => {
  const $=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let accessData, profile, snapshot, generation=0, busy=false, catalogComplete=true, employeeRows=[];
  const protectedSettings = () => window.tenantSharedIntegrations.scoped(accessData,'linear') && profile?.role!=='owner';
  async function api(params='',data) {
    const response=await fetch('/api/platform/linear/settings'+params,{credentials:'same-origin',cache:'no-store',
      ...(data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});
    const result=await response.json();
    if(!response.ok||!result.success)throw new Error(result.error||'Ayarlar yüklənmədi.');
    if(data)window.tenantSharedIntegrations.record(accessData,'linear',result);
    return result;
  }
  function disable(on){busy=on;$('tenantLinearPanel')?.querySelectorAll('button,input,select').forEach(input=>input.disabled=on||input.dataset.tlReadonly==='true'||!!input.closest('[data-tl-blocked="true"]'));if($('tlSave'))$('tlSave').disabled=on||!catalogComplete;const shared=window.tenantSharedIntegrations;if(shared.scoped(accessData,'linear'))$('tenantLinearPanel')?.querySelectorAll('button,input,select').forEach(input=>{const action=input.id==='tlLoad'||input.id==='tlMore'?'view':input.id==='tlDisconnect'?'delete':input.id==='tlConnect'||input.id==='tlKey'?(shared.connected(accessData,'linear')?'update':'create'):'update';input.disabled=input.disabled||!shared.can(accessData,'linear',action)||(protectedSettings()&&!!input.closest('[data-tl-owner-settings]'));});}
  async function perform(work){if(busy)return;const current=generation;disable(true);$('tlMessage').textContent='Yüklənir…';
    try{await work(current);if(current===generation)$('tlMessage').textContent='Hazırdır.';}
    catch(error){if(current===generation)$('tlMessage').textContent=error.message;}
    finally{if(current===generation)disable(false);}}
  const employeeEvents=new Set(['linear_done','linear_status_changed','proactive_risk']);
  async function memberApi(data){
    const response=await fetch('/api/platform/members',{credentials:'same-origin',cache:'no-store',
      ...(data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});
    const result=await response.json();
    if(!response.ok||!result.success)throw new Error(result.error||'Əməkdaş ayarları yüklənmədi.');
    return result;
  }
  function employeeControls(row){
    if(!row?.card_version)return '<p>Əməkdaş ayarlarını yükləmək üçün siyahını yeniləyin.</p>';
    const grantEditable=row.role!=='owner'&&row.editable_fields?.includes('permission_changes');
    const settingsEditable=row.editable_fields?.includes('work_settings')&&!!row.work_settings?.version;
    const readonly=allowed=>allowed?'':'disabled data-tl-readonly="true"';
    return `<div data-tl-employee="${esc(row.telegram_id)}"><h4>Linear girişi və şəxsi bildirişlər</h4>
      <label style="display:flex"><input type="checkbox" style="width:auto" data-tl-grant ${readonly(grantEditable)} ${(row.permissions||[]).includes('linear')||row.role==='owner'?'checked':''}>Linear səhifəsinə giriş</label>
      <p data-tl-partial hidden>Məhdud Linear icazələri saxlanılır. Dəyişmək bütün Linear səhifəsinə girişi dəyişir.</p>
      ${(row.work_settings?.notifications||[]).filter(event=>employeeEvents.has(event.key)).map(event=>`<div><span>${esc(event.label)}</span> ${event.channels.map(channel=>`<label style="display:inline-flex"><input type="checkbox" style="width:auto" data-tl-event="${esc(event.key)}" data-tl-channel="${esc(channel)}" ${readonly(settingsEditable)} ${row.work_settings?.values?.notifications?.[event.key]?.[channel]===true?'checked':''}>${esc(channel)}</label>`).join('')}</div>`).join('')}
      <button type="button" class="outline" data-tl-save ${readonly(grantEditable||settingsEditable)}>Bu əməkdaşın giriş və bildirişlərini saxla</button>
      <p data-tl-result role="status" aria-live="polite"></p></div>`;
  }
  function bindEmployeeControls(root,row){
    const grant=root.querySelector('[data-tl-grant]');
    if(!grant)return;
    grant.indeterminate=row.role!=='owner'&&!row.permissions?.includes('linear')&&(row.permissions||[]).some(key=>key.startsWith('linear:'));
    root.querySelector('[data-tl-partial]').hidden=!grant.indeterminate;
    grant.onchange=()=>{grant.dataset.touched='true';};
    root.querySelector('[data-tl-save]').onclick=()=>saveEmployee(root,row);
  }
  function employeeChanges(root,row){
    const changes={}, grant=root.querySelector('[data-tl-grant]');
    if(grant.dataset.touched==='true'&&grant.dataset.tlReadonly!=='true'&&
       (grant.checked!==(row.permissions||[]).includes('linear')||(row.permissions||[]).some(key=>key.startsWith('linear:')))) {
      changes.permission_changes={linear:grant.checked};
    }
    const notifications={};
    root.querySelectorAll('[data-tl-event]').forEach(input=>{
      const event=input.dataset.tlEvent, channel=input.dataset.tlChannel;
      if(input.dataset.tlReadonly==='true'||!employeeEvents.has(event))return;
      if(input.checked!==(row.work_settings?.values?.notifications?.[event]?.[channel]===true)) {
        (notifications[event]??={})[channel]=input.checked;
      }
    });
    if(Object.keys(notifications).length)changes.work_settings={expected_version:row.work_settings.version,changes:{notifications}};
    return changes;
  }
  function requireSavedEmployeeControls(){
    const pending=[...$('tlMembers').querySelectorAll('[data-tl-employee]')].some(root=>{
      const row=employeeRows.find(item=>String(item.telegram_id)===root.dataset.tlEmployee);
      return root.dataset.tlBlocked==='true'||(row&&Object.keys(employeeChanges(root,row)).length>0);
    });
    if(pending)throw new Error('Əvvəlcə əməkdaşın giriş və bildiriş dəyişikliklərini ayrıca saxlayın. Saxlama nəticəsi məlum deyilsə, ayarları yenidən yükləyin.');
  }
  async function saveEmployee(root,row){
    if(busy||profile?.role!=='owner'||root.dataset.tlBlocked==='true'||!root.isConnected)return;
    const changes=employeeChanges(root,row),message=root.querySelector('[data-tl-result]');
    if(!Object.keys(changes).length){message.textContent='Dəyişiklik yoxdur.';return;}
    const current=generation, identity=profile.identity;
    const command={action:'update_member_card',telegram_id:row.telegram_id,expected_tenant_id:profile.tenant_id,
      expected_user_id:profile.telegram_id,expected_version:row.card_version,changes};
    const valid=()=>current===generation&&profile?.identity===identity&&root.isConnected;
    disable(true);message.textContent='Saxlanılır…';let committed=false;
    try{
      const result=await memberApi(command);if(!valid())return;
      const receipt=result.applied;
      if(!receipt||String(receipt.telegram_id)!==String(row.telegram_id)||typeof receipt.card_version!=='string'||!receipt.card_version)throw new Error('Saxlama nəticəsi təsdiqlənmədi.');
      committed=true;message.textContent='Saxlanıldı. Siyahı yenilənir…';
      const fresh=await memberApi();if(!valid())return;
      const updated=(fresh.members||[]).find(item=>String(item.telegram_id)===String(row.telegram_id));
      if(!updated?.card_version)throw new Error('Əməkdaşın yeni ayarları yüklənmədi.');
      employeeRows=employeeRows.map(item=>String(item.telegram_id)===String(row.telegram_id)?updated:item);
      const wrapper=document.createElement('div');wrapper.innerHTML=employeeControls(updated);
      const replacement=wrapper.firstElementChild;root.replaceWith(replacement);bindEmployeeControls(replacement,updated);
      replacement.querySelector('[data-tl-result]').textContent='Giriş və bildirişlər saxlanıldı.';
    }catch(error){if(valid()){root.dataset.tlBlocked='true';message.textContent=(committed?'Saxlanıldı, amma yeni ayarlar yüklənmədi. ':error.message+' ')+'Davam etmək üçün ayarları yenidən yükləyin.';}}
    finally{if(current===generation&&profile?.identity===identity)disable(false);}
  }
  function install(data){accessData=data;const next=data.member;
    const identity=next.tenant_id+':'+next.telegram_id+':'+next.role;
    if(profile?.identity===identity)return;
    generation++;profile={...next,identity};snapshot=null;busy=false;catalogComplete=true;$('tenantLinearPanel')?.remove();
    if(window.tenantSharedIntegrations.scoped(data,'linear')?!Object.values(window.tenantSharedIntegrations.row(data,'linear').management_actions).some(Boolean):next.role!=='owner')return;
    $('view-settings').insertAdjacentHTML('beforeend',`<article class="panel" id="tenantLinearPanel" style="margin-top:16px">
      <h2>Linear və xəbərlər</h2><p>Şirkətə məxsus bağlantı, görünüş və iş qaydaları. Xəbərlər ayrıca yoxlanılır və təsdiqlənir. Köhnə kabinet dəyişmir.</p>
      <button class="outline" id="tlLoad">Ayarları yüklə</button><div id="tlBody" hidden>
      <p id="tlStatus"></p><label>Linear API açarı<input id="tlKey" type="password" autocomplete="new-password" placeholder="Yeni açar (saxlanmış açar göstərilmir)"></label>
      <button class="outline" id="tlConnect">Yoxla və qoş</button> <button class="outline danger" id="tlDisconnect">Bağlantını ayır</button>
      <label>Komanda<select id="tlTeam"><option value="">Seçin</option></select></label><button class="outline" id="tlMore" hidden>Daha çox komanda</button>
      <div id="tlStates"></div><div data-tl-owner-settings><div id="tlProjects"></div><div id="tlRules"></div><div id="tlMembers"></div>
      <label style="display:flex"><input id="tlNewsEnabled" type="checkbox" style="width:auto">Tamamlanmış əsas tapşırıqlardan xəbər qaralamaları hazırla</label>
      <p>Fon sinxronizasiyasını da aktiv edin və layihələri / tamamlanmış statusları seçin. «Xəbərlər» səhifəsində yoxlayıb dərc edin. AI emalı ayrıca «Xəbərlər üçün AI» ayarlarında aktiv edilir; onsuz mənbə fraqmenti saxlanılır.</p>
      <label>Əvvəlki kanal qeydi (göndəriş üçün ayrıca «Telegram xəbər kanalı» bölməsində yoxlayın)<input id="tlChannel" placeholder="@kanal və ya -100…"></label>
      <p>Yalnız əsas tapşırıqlar, 90 günlük saxlama və dərcdən əvvəl əl ilə təsdiq. Kanalın yazılması mesaj göndərmir. Account/Operator mövcud Linear dəyərləridir; yeni parametr yaradılmır.</p>
      </div><p data-tl-owner-notice hidden>Əməkdaş, xəbər və iş qaydalarını yalnız şirkət sahibi dəyişə bilər. Saxlanmış qaydalar qorunur; komanda dəyişikliyi onlarla uyğun olmalıdır.</p><button class="primary" id="tlSave">Ayarları saxla</button></div><p id="tlMessage" role="status" aria-live="polite"></p></article>`);
    $('tenantLinearPanel').querySelector('[data-tl-owner-settings]').hidden=protectedSettings();
    $('tenantLinearPanel').querySelector('[data-tl-owner-notice]').hidden=!protectedSettings();
    $('tlLoad').onclick=()=>perform(load);
    $('tlConnect').onclick=()=>perform(async current=>{requireSavedEmployeeControls();const key=$('tlKey').value;$('tlKey').value='';await write('connect',{api_key:key});if(current===generation)await load(current);});
    $('tlDisconnect').onclick=()=>{if(confirm('Linear bağlantısı ayrılsın?'))perform(async current=>{await write('disconnect');if(current===generation)await load(current);});};
    $('tlTeam').onchange=()=>perform(teamChoices);
    $('tlMore').onclick=()=>perform(current=>teams(current,$('tlMore').dataset.cursor));
    disable(false);
    $('tlSave').onclick=()=>perform(async current=>{const config={team_id:$('tlTeam').value,
      done_state_ids:[...$('tlStates').querySelectorAll('input:checked')].map(i=>i.value),
      workflow:protectedSettings()?snapshot.settings?.workflow:window.TenantLinearRules.read(),
      members:protectedSettings()?snapshot.settings?.members:Object.fromEntries([...$('tlMembers').querySelectorAll('[data-member]')].map(row=>[row.dataset.member,
        Object.fromEntries([...row.querySelectorAll('[data-field]')].map(i=>[i.dataset.field,i.multiple?[...i.selectedOptions].map(o=>o.value):i.type==='checkbox'?i.checked:i.value]))])),
      news:protectedSettings()?snapshot.settings?.news:{enabled:$('tlNewsEnabled').checked,channel:$('tlChannel').value.trim(),projects:[...$('tlProjects').querySelectorAll('input:checked')].map(i=>i.value)}};
      await write('settings',{settings:config});if(current===generation)await load(current);});
    if(window.tenantSharedIntegrations.scoped(data,'linear'))void perform(load);
  }
  async function write(action,extra={}){requireSavedEmployeeControls();if(!snapshot)throw new Error('Əvvəlcə ayarları yükləyin.');
    return api('',window.tenantSharedIntegrations.command(accessData,'linear',{action,...extra,expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id,expected_updated_at:snapshot.updated_at},action==='disconnect'?'delete':action==='connect'?(window.tenantSharedIntegrations.connected(accessData,'linear')?'update':'create'):'update'));}
  async function teams(current,after){const result=await api('?catalog=1'+(after?'&after='+encodeURIComponent(after):''));if(current!==generation)return;
    result.teams.nodes.forEach(team=>{if(![...$('tlTeam').options].some(option=>option.value===team.id))$('tlTeam').insertAdjacentHTML('beforeend',`<option value="${esc(team.id)}">${esc(team.name)}</option>`);});
    $('tlMore').hidden=!result.teams.pageInfo.hasNextPage;$('tlMore').dataset.cursor=result.teams.pageInfo.endCursor||'';}
  async function teamChoices(current){catalogComplete=false;$('tlStates').innerHTML='';$('tlProjects').innerHTML='';const team=$('tlTeam').value;if(!team){catalogComplete=true;window.TenantLinearRules.render({},null);return;}
    const result=await api('?catalog=1&team_id='+encodeURIComponent(team));if(current!==generation)return;
    const prior=snapshot.settings||{};const matching=prior.team_id===team;
    for(const [target,items,selected,title] of [['tlStates',result.team.states.nodes.filter(s=>s.type==='completed'),matching?prior.done_state_ids:[],'Xəbər mənbəyi: tamamlanmış statuslar'],
      ['tlProjects',result.team.projects.nodes,matching?prior.news?.projects:[],'Xəbər layihələri']]){
      $(target).innerHTML=`<h3>${esc(title)}</h3>`+items.map(item=>`<label style="display:flex;align-items:center"><input style="width:auto" type="checkbox" value="${esc(item.id)}" ${(selected||[]).includes(item.id)?'checked':''}>${esc(item.name)}</label>`).join('');
    }
    window.TenantLinearRules.render(matching?prior:{members:prior.members},result.team);
    catalogComplete=!(result.team.states.pageInfo.hasNextPage||result.team.projects.pageInfo.hasNextPage||result.team.members.pageInfo.hasNextPage);
    if(!catalogComplete)$('tlProjects').insertAdjacentHTML('beforeend','<p>İlk 50 seçim göstərilir. Ayarları itirməmək üçün saxlama dayandırılıb: böyük kataloq üçün genişləndirilmiş səhifələmə tələb olunur.</p>');
  }
  async function load(current){if(window.tenantSharedIntegrations.scoped(accessData,'linear')&&!window.tenantSharedIntegrations.can(accessData,'linear','view')){snapshot={updated_at:window.tenantSharedIntegrations.row(accessData,'linear').updated_at||''};$('tlBody').hidden=false;for(const node of $('tlBody').children)node.hidden=!['tlStatus','tlConnect','tlDisconnect'].includes(node.id)&&!node.querySelector?.('#tlKey');disable(false);return;}const [result, employees]=await Promise.all([api(),profile.role==='owner'?memberApi():Promise.resolve({members:[]})]);if(current!==generation)return;employeeRows=employees.members||[];snapshot=result;window.tenantViewDemand.clean('#tenantLinearPanel');$('tlBody').hidden=false;$('tlKey').value='';
    $('tlStatus').textContent=result.status==='connected'?(result.runtime_enabled?'Linear tapşırıqları aktivdir':'Linear qoşulub · iş qaydalarını aktiv edin'):'Linear qoşulmayıb';
    const config=result.settings||{};catalogComplete=!config.team_id;$('tlChannel').value=config.news?.channel||'';$('tlNewsEnabled').checked=config.news?.enabled===true;
    $('tlMembers').innerHTML='<h3>Əməkdaşların Linear qaydaları</h3>'+result.members.map(member=>{
      const binding=config.members?.[member.telegram_id]||{};
      return `<details style="margin:12px 0" data-member="${esc(member.telegram_id)}"><summary>${esc(member.display_name||'Əməkdaş')}</summary>`+
        ['account','operator'].map(field=>`<label>${field==='account'?'Account':'Operator'}<input data-field="${field}" value="${esc(binding[field]||'')}"></label>`).join('')+
        '<label>Linear icraçısı<select data-field="assignee_id"><option value="">Təyin edilməyib</option></select></label>'+
        [['can_view_all','Komandanın bütün tapşırıqlarını görmək'],['can_create','Tapşırıq yaratmaq'],['can_edit','Tapşırıq redaktəsi'],['can_change_status','Sərbəst status dəyişmək'],['can_review_news','Xəbərləri təsdiqləmək və dərc etmək']].map(([field,label])=>`<label style="display:flex"><input style="width:auto" type="checkbox" data-field="${field}" ${binding[field]?'checked':''}>${label}</label>`).join('')+
        '<label>İcazəli keçid düymələri<select data-field="button_ids" multiple style="min-height:80px"></select></label><p>Linear səhifəsi şirkət modullarında və əməkdaşın səhifə icazələrində də açıq olmalıdır.</p>'+employeeControls(employeeRows.find(row=>String(row.telegram_id)===String(member.telegram_id)))+'</details>';
    }).join('');
    $('tlMembers').querySelectorAll('[data-tl-employee]').forEach(root=>bindEmployeeControls(root,employeeRows.find(row=>String(row.telegram_id)===root.dataset.tlEmployee)));
    $('tlTeam').innerHTML='<option value="">Seçin</option>';$('tlStates').innerHTML='';$('tlProjects').innerHTML='';$('tlMore').hidden=true;
    window.TenantLinearRules.render(config,null);
    if(result.status==='connected'){await teams(current);if(current!==generation)return;
      if(config.team_id){if(![...$('tlTeam').options].some(o=>o.value===config.team_id))$('tlTeam').insertAdjacentHTML('beforeend',`<option value="${esc(config.team_id)}">Saxlanmış komanda</option>`);
        $('tlTeam').value=config.team_id;await teamChoices(current);}}
  }
  window.tenantViewDemand.watch({provider:'linear', editable:'#tenantLinearPanel', activate:install,
    reset:()=>{generation++;profile=null;busy=false;$('tenantLinearPanel')?.remove();}});
})();
