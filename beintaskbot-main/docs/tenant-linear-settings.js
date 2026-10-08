/* Tenant configuration only: no issues written and no news published here. */
(() => {
  const $=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let profile, snapshot, generation=0, busy=false, catalogComplete=true;
  async function api(params='',data) {
    const response=await fetch('/api/platform/linear/settings'+params,{credentials:'same-origin',cache:'no-store',
      ...(data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});
    const result=await response.json();
    if(!response.ok||!result.success)throw new Error(result.error||'Ayarlar yüklənmədi.');
    return result;
  }
  function disable(on){busy=on;$('tenantLinearPanel')?.querySelectorAll('button,input,select').forEach(input=>input.disabled=on);if($('tlSave'))$('tlSave').disabled=on||!catalogComplete;}
  async function perform(work){if(busy)return;const current=generation;disable(true);$('tlMessage').textContent='Yüklənir…';
    try{await work(current);if(current===generation)$('tlMessage').textContent='Hazırdır.';}
    catch(error){if(current===generation)$('tlMessage').textContent=error.message;}
    finally{if(current===generation)disable(false);}}
  function install(data){const next=data.member;
    const identity=next.tenant_id+':'+next.telegram_id+':'+next.role;
    if(profile?.identity===identity)return;
    generation++;profile={...next,identity};snapshot=null;busy=false;catalogComplete=true;$('tenantLinearPanel')?.remove();
    if(next.role!=='owner')return;
    $('view-settings').insertAdjacentHTML('beforeend',`<article class="panel" id="tenantLinearPanel" style="margin-top:16px">
      <h2>Linear və xəbərlər</h2><p>Şirkətə məxsus bağlantı, görünüş və iş qaydaları. Xəbərlər ayrıca yoxlanılır və təsdiqlənir. Köhnə kabinet dəyişmir.</p>
      <button class="outline" id="tlLoad">Ayarları yüklə</button><div id="tlBody" hidden>
      <p id="tlStatus"></p><label>Linear API açarı<input id="tlKey" type="password" autocomplete="new-password" placeholder="Yeni açar (saxlanmış açar göstərilmir)"></label>
      <button class="outline" id="tlConnect">Yoxla və qoş</button> <button class="outline danger" id="tlDisconnect">Bağlantını ayır</button>
      <label>Komanda<select id="tlTeam"><option value="">Seçin</option></select></label><button class="outline" id="tlMore" hidden>Daha çox komanda</button>
      <div id="tlStates"></div><div id="tlProjects"></div><div id="tlRules"></div><div id="tlMembers"></div>
      <label style="display:flex"><input id="tlNewsEnabled" type="checkbox" style="width:auto">Tamamlanmış əsas tapşırıqlardan xəbər qaralamaları hazırla</label>
      <p>Fon sinxronizasiyasını da aktiv edin və layihələri / tamamlanmış statusları seçin. «Xəbərlər» səhifəsində yoxlayıb dərc edin. AI emalı ayrıca «Xəbərlər üçün AI» ayarlarında aktiv edilir; onsuz mənbə fraqmenti saxlanılır.</p>
      <label>Telegram xəbər kanalı (gələcək göndəriş üçün)<input id="tlChannel" placeholder="@kanal və ya -100…"></label>
      <p>Yalnız əsas tapşırıqlar, 90 günlük saxlama və dərcdən əvvəl əl ilə təsdiq. Kanalın yazılması mesaj göndərmir. Account/Operator mövcud Linear dəyərləridir; yeni parametr yaradılmır.</p>
      <button class="primary" id="tlSave">Ayarları saxla</button></div><p id="tlMessage" role="status" aria-live="polite"></p></article>`);
    $('tlLoad').onclick=()=>perform(load);
    $('tlConnect').onclick=()=>perform(async current=>{const key=$('tlKey').value;$('tlKey').value='';await write('connect',{api_key:key});if(current===generation)await load(current);});
    $('tlDisconnect').onclick=()=>{if(confirm('Linear bağlantısı ayrılsın?'))perform(async current=>{await write('disconnect');if(current===generation)await load(current);});};
    $('tlTeam').onchange=()=>perform(teamChoices);
    $('tlMore').onclick=()=>perform(current=>teams(current,$('tlMore').dataset.cursor));
    $('tlSave').onclick=()=>perform(async current=>{const config={team_id:$('tlTeam').value,
      done_state_ids:[...$('tlStates').querySelectorAll('input:checked')].map(i=>i.value),
      workflow:window.TenantLinearRules.read(),
      members:Object.fromEntries([...$('tlMembers').querySelectorAll('[data-member]')].map(row=>[row.dataset.member,
        Object.fromEntries([...row.querySelectorAll('[data-field]')].map(i=>[i.dataset.field,i.multiple?[...i.selectedOptions].map(o=>o.value):i.type==='checkbox'?i.checked:i.value]))])),
      news:{enabled:$('tlNewsEnabled').checked,channel:$('tlChannel').value.trim(),projects:[...$('tlProjects').querySelectorAll('input:checked')].map(i=>i.value)}};
      await write('settings',{settings:config});if(current===generation)await load(current);});
  }
  async function write(action,extra={}){if(!snapshot)throw new Error('Əvvəlcə ayarları yükləyin.');
    return api('',{action,...extra,expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id,expected_updated_at:snapshot.updated_at});}
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
  async function load(current){const result=await api();if(current!==generation)return;snapshot=result;$('tlBody').hidden=false;$('tlKey').value='';
    $('tlStatus').textContent=result.status==='connected'?(result.runtime_enabled?'Linear tapşırıqları aktivdir':'Linear qoşulub · iş qaydalarını aktiv edin'):'Linear qoşulmayıb';
    const config=result.settings||{};catalogComplete=!config.team_id;$('tlChannel').value=config.news?.channel||'';$('tlNewsEnabled').checked=config.news?.enabled===true;
    $('tlMembers').innerHTML='<h3>Əməkdaşların Linear qaydaları</h3>'+result.members.map(member=>{
      const binding=config.members?.[member.telegram_id]||{};
      return `<details style="margin:12px 0" data-member="${esc(member.telegram_id)}"><summary>${esc(member.display_name||'Əməkdaş')}</summary>`+
        ['account','operator'].map(field=>`<label>${field==='account'?'Account':'Operator'}<input data-field="${field}" value="${esc(binding[field]||'')}"></label>`).join('')+
        '<label>Linear icraçısı<select data-field="assignee_id"><option value="">Təyin edilməyib</option></select></label>'+
        [['can_view_all','Komandanın bütün tapşırıqlarını görmək'],['can_create','Tapşırıq yaratmaq'],['can_edit','Tapşırıq redaktəsi'],['can_change_status','Sərbəst status dəyişmək'],['can_review_news','Xəbərləri təsdiqləmək və dərc etmək']].map(([field,label])=>`<label style="display:flex"><input style="width:auto" type="checkbox" data-field="${field}" ${binding[field]?'checked':''}>${label}</label>`).join('')+
        '<label>İcazəli keçid düymələri<select data-field="button_ids" multiple style="min-height:80px"></select></label><p>Linear səhifəsi şirkət modullarında və əməkdaşın səhifə icazələrində də açıq olmalıdır.</p></details>';
    }).join('');
    $('tlTeam').innerHTML='<option value="">Seçin</option>';$('tlStates').innerHTML='';$('tlProjects').innerHTML='';$('tlMore').hidden=true;
    window.TenantLinearRules.render(config,null);
    if(result.status==='connected'){await teams(current);if(current!==generation)return;
      if(config.team_id){if(![...$('tlTeam').options].some(o=>o.value===config.team_id))$('tlTeam').insertAdjacentHTML('beforeend',`<option value="${esc(config.team_id)}">Saxlanmış komanda</option>`);
        $('tlTeam').value=config.team_id;await teamChoices(current);}}
  }
  document.addEventListener('tenant-profile',event=>install(event.detail));const initial=generation;
  fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(initial===generation&&data?.member)install(data);}).catch(()=>{});
})();
