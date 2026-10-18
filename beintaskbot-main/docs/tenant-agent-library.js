/* Library owns the creation flow, never the AI Agents header or runtime. */
(()=>{
 'use strict';
 if(!document.getElementById('view-ai_agents'))return;
 const $=id=>document.getElementById(id),catalog=window.tenantAgentCatalog;
 const endpoint=window.TENANT_AI_AGENTS_CONFIG?.endpoint||'/api/platform/ai/';
 const t=s=>window.tenantNightL10n?.text(s)||s;
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let profileStamp='',member=null,epoch=0,request=0,rows=[],models=[],allowed=false,category='all',channelFilter='all',returnFocus=null;
 const modal=document.createElement('div');modal.id='agentTemplateLibrary';modal.className='ai-agents-modal';modal.hidden=true;
 modal.innerHTML=`<div class="ai-agents-modal-backdrop" data-library-close></div><section class="ai-agents-dialog agent-library-dialog" role="dialog" aria-modal="true" aria-labelledby="agentLibraryTitle" tabindex="-1"><header class="ai-agents-dialog-head"><div><h2 id="agentLibraryTitle">Создать AI-агента</h2><p class="sub">Выберите готовый шаблон или начните с нуля.</p></div><button type="button" class="outline" data-library-close>Закрыть</button></header><div class="ai-agents-dialog-body"><p id="agentLibraryNotice" role="status" aria-live="polite"></p><button type="button" id="agentLibraryRetry" class="outline">Обновить подключения</button><button type="button" id="agentLibraryBlank" class="agent-library-blank"><span class="agent-library-plus" aria-hidden="true">+</span><span><b>Создать с нуля</b><small>Пустые инструкции, ваши каналы, график и правила.</small></span><span aria-hidden="true">→</span></button><div class="agent-library-layout"><aside class="agent-library-categories" aria-label="Категории шаблонов" id="agentLibraryCategories"></aside><section class="agent-library-results" aria-labelledby="agentLibraryResultsTitle"><div class="agent-library-filters"><h3 id="agentLibraryResultsTitle">Готовые шаблоны</h3><label>Поддерживаемые каналы<select id="agentLibraryChannelFilter"></select></label></div><div id="agentLibraryCards"></div></section></div><p class="agent-library-safety">Новый агент выключен и работает в Manual. Каналы выбираются явно. Настройки компании не меняются.</p></div></section>`;
 document.body.append(modal);
 const notice=message=>{$('agentLibraryNotice').textContent=t(message||'')};
 function close(){modal.hidden=true;request++;returnFocus?.focus?.();returnFocus=null;}
 function reset(){epoch++;request++;allowed=false;rows=[];models=[];member=null;close();$('agentLibraryCards').replaceChildren();$('agentLibraryCategories').replaceChildren();$('agentLibraryChannelFilter').replaceChildren();$('agentLibraryBlank').disabled=true;notice('');}
 document.addEventListener('tenant-profile',event=>{const stamp=JSON.stringify({member:event.detail?.member,allowed:event.detail?.capabilities?.modules?.ai_agents});if(stamp!==profileStamp){profileStamp=stamp;reset()}});
 async function profile(){
  const version=epoch,r=await fetch(window.TENANT_AI_AGENTS_CONFIG?.profileUrl||'/api/platform/me',{credentials:'same-origin',cache:'no-store'}),data=await r.json().catch(()=>null);
  if(version!==epoch)throw new Error('Компания изменилась. Откройте библиотеку заново.');
  if(!r.ok||!data?.member||data.capabilities?.modules?.ai_agents===false)throw new Error('Недостаточно прав для создания AI-агента.');
  profileStamp=JSON.stringify({member:data.member,allowed:data.capabilities?.modules?.ai_agents});member=data.member;return member;
 }
 async function get(path,current,version){
  const q=new URLSearchParams({expected_tenant_id:current.tenant_id,expected_user_id:current.telegram_id});
  const r=await fetch(endpoint+path+(path.includes('?')?'&':'?')+q,{credentials:'same-origin',cache:'no-store'}),data=await r.json().catch(()=>null);
  if(version!==epoch)throw new Error('Компания изменилась. Откройте библиотеку заново.');
  if(!r.ok||!data?.success)throw new Error(r.status===403?'Недостаточно прав для создания AI-агента.':data?.error||(path==='channels'?'Каталог подключений недоступен. Обновите страницу или обратитесь к администратору.':'Не удалось загрузить библиотеку.'));
  return data.result;
 }
 function render(){
  const types=catalog.available(rows),ready=allowed&&models.length>0;
  $('agentLibraryBlank').disabled=!ready||!types.length;
  $('agentLibraryCategories').innerHTML=[{id:'all',name:'Все категории'},...catalog.categories].map(item=>`<button type="button" data-library-category="${item.id}" aria-pressed="${item.id===category}"><span>${esc(t(item.name))}</span><small>${catalog.templates.filter(template=>template.ready&&(item.id==='all'||template.category===item.id)).length}</small></button>`).join('');
  $('agentLibraryCategories').querySelectorAll('button').forEach(button=>button.onclick=()=>{category=button.dataset.libraryCategory;render()});
  const select=$('agentLibraryChannelFilter');select.innerHTML=`<option value="all">${esc(t('Все каналы'))}</option>`+types.map(row=>`<option value="${esc(row.id)}" data-user-content>${esc(catalog.connectionName(row))}</option>`).join('');select.value=channelFilter;if(select.value!==channelFilter){channelFilter='all';select.value='all';}
  // Unavailable integrations never make a template launchable. Implemented cards remain discoverable.
  const cards=catalog.templates.filter(item=>item.ready&&(category==='all'||item.category===category));
  $('agentLibraryCards').innerHTML=cards.length?cards.map(item=>{
   const supported=types.filter(row=>(channelFilter==='all'||row.id===channelFilter)&&item.providers.includes(row.provider)),canUse=ready&&supported.length>0;
   return `<article class="agent-template-card"><div class="agent-template-icon" aria-hidden="true">◈</div><small class="agent-template-category">${esc(t(catalog.categories.find(c=>c.id===item.category)?.name||''))}</small><h4>${esc(t(item.name))}</h4><p>${esc(t(item.description))}</p><div class="agent-template-features"><span>${esc(t('База знаний'))}</span><span>${esc(t('Передача человеку'))}</span><span>${esc(t('Расписание'))}</span></div><p class="agent-template-channels">${supported.length?supported.map(row=>`<span data-user-content>${esc(catalog.connectionName(row))}</span>`).join(' · '):esc(t('Нет доступных поддерживаемых подключений.'))}</p><button type="button" class="primary" data-agent-template="${item.id}" ${canUse?'':'disabled'}>${esc(t('Выбрать шаблон'))}</button></article>`;
  }).join(''):`<div class="agent-library-empty"><b>${esc(t('В этой категории пока нет готовых шаблонов.'))}</b><p>${esc(t('Создайте агента с нуля или выберите другую категорию.'))}</p></div>`;
  $('agentLibraryCards').querySelectorAll('[data-agent-template]').forEach(button=>button.onclick=()=>launch(button.dataset.agentTemplate));
  window.tenantNightL10n?.translate(modal);
 }
 async function open(){
  if(!modal.hidden)return;
  returnFocus=document.activeElement;$('aiAgentsModalClose')?.click();modal.hidden=false;modal.querySelector('[role=dialog]').focus();category='all';channelFilter='all';rows=[];models=[];allowed=false;render();notice('Загружаю доступные подключения и модели…');
  const version=epoch,serial=++request;$('agentLibraryRetry').disabled=true;
  try{
   const current=await profile(),data=await Promise.all([get('channels',current,version),get('agents/options?cached=1',current,version)]);
   if(version!==epoch||serial!==request||modal.hidden)return;
   rows=catalog.parse(data[0]);models=Array.isArray(data[1]?.models)?data[1].models:[];allowed=true;render();
   notice(!catalog.available(rows).length?'Нет доступных поддерживаемых подключений.':!models.length?'Нет разрешённых моделей — завершите настройку Chat AI':'');
  }catch(error){if(version===epoch&&serial===request){allowed=false;rows=[];models=[];render();notice(error.message)}}
  finally{if(version===epoch&&serial===request){$('agentLibraryRetry').disabled=false;($('agentLibraryBlank').disabled?$('agentLibraryRetry'):$('agentLibraryBlank')).focus()}}
 }
 function launch(templateId){
  if(!allowed||!models.length||!catalog.available(rows).length)return;
  if(templateId!=='blank'&&!catalog.templates.some(item=>item.ready&&item.id===templateId))return;
  if(!window.tenantAgentWizard?.open){notice('Мастер создания недоступен. Обновите страницу.');return;}
  close();window.tenantAgentWizard.open({templateId,channelIds:channelFilter==='all'?[]:[channelFilter]});
 }
 $('agentLibraryBlank').onclick=()=>launch('blank');$('agentLibraryChannelFilter').onchange=()=>{channelFilter=$('agentLibraryChannelFilter').value;render()};
 $('agentLibraryRetry').onclick=()=>{close();open()};modal.querySelectorAll('[data-library-close]').forEach(node=>node.onclick=close);
 document.addEventListener('keydown',event=>{if(modal.hidden)return;if(event.key==='Escape'){event.preventDefault();close()}if(event.key==='Tab'){const nodes=[...modal.querySelectorAll('button:not([disabled]),select:not([disabled])')].filter(n=>!n.hidden),first=nodes[0],last=nodes.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}}});
 document.addEventListener('click',event=>{const button=event.target.closest?.('[data-ai-overview-create],#aiNewAgent');if(!button||button.disabled)return;event.preventDefault();event.stopImmediatePropagation();window.tenantAgentWizard.open()},true);
 document.addEventListener('tenant-ai-create-request',()=>window.tenantAgentWizard.open());
 document.addEventListener('tenant-language',()=>{if(!modal.hidden){render();window.tenantNightL10n?.translate(modal)}});
 window.tenantAgentLibrary=Object.freeze({open});
})();
