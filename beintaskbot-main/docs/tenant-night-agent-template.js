/* Guided setup for the existing tenant-scoped AI Agents API. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id);
 const config=window.TENANT_AI_AGENTS_CONFIG||{};
 const endpoint=config.endpoint||'/api/platform/ai/';
 const DEFAULT_MODEL='__platform_default__';
 const DEFAULT_HANDOFF='оператор | менеджер | человек | авария | критическая ошибка | не работает';
 const DEFAULT_INSTRUCTIONS=`Вы — вежливый AI-оператор ночной технической поддержки.\n\nРаботайте только в выбранные дни и часы. Поздоровайтесь в первом ответе активной сессии и отвечайте на языке клиента. Отвечайте только на основе доступной базы знаний и этих инструкций. Если данных недостаточно, задайте короткий уточняющий вопрос или честно скажите, что решение неизвестно.\n\nНе выдумывайте решения, инструкции, сроки, цены, статусы или результаты действий. Для сложных, аварийных или нерешённых проблем сообщайте клиенту, что обращение передаётся человеку, и не обещайте невозможного. Условия передачи ниже создадут существующий handoff-триггер: он фиксирует нерешённое обращение в текущем контуре поддержки.`;
 const STEPS=['Название','WhatsApp-канал','Часовой пояс','Ночная смена','Модель','Инструкции','База знаний','Передача человеку','Режим','Проверка'];
 let profile=null,options=null,settings=null,knowledgeSources=[],returnFocus=null,createdAgent=null,triggerSaved=false,uploadedFileSignature='';

 function option(label,value,selected=false){const node=document.createElement('option');node.value=value;node.textContent=label;node.selected=selected;return node}
 function setNotice(message,state=''){const node=$('nightAgentNotice');if(!node)return;node.textContent=message||'';node.dataset.state=state}
 function showError(message){setNotice(message,'warn')}
 function selectedDays(){return [...document.querySelectorAll('[name=nightAgentDay]:checked')].map(node=>Number(node.value)).sort((a,b)=>a-b)}
 function fileSignature(file){return file?`${file.name}:${file.size}:${file.lastModified}`:''}

 // Kept independent from the DOM so the setup contract is regression-tested.
 function nightAgentSchedule(timezone,days,start,end){
  const zone=String(timezone||'').trim();
  const selected=(Array.isArray(days)?days:[]).filter(day=>Number.isInteger(day)&&day>=0&&day<=6);
  if(!zone||!selected.length||!/^([01]\d|2[0-3]):[0-5]\d$/.test(start||'')||!/^([01]\d|2[0-3]):[0-5]\d$/.test(end||'')||start===end)throw new Error('Укажите корректный часовой пояс, дни и разные часы начала и окончания.');
  return {timezone:zone,days:[...new Set(selected)].sort((a,b)=>a-b),start,end};
 }
 function nightAgentScope(channelId){
  const channel=String(channelId||'').trim();
  if(!channel)throw new Error('Выберите подключённый WhatsApp-канал.');
  return [{provider:'connect',pipeline_id:'',stage_id:'',channel_id:channel,instructions:''}];
 }
 function nightAgentPayload(values){
  const mode=values.mode==='automatic'?'automatic':'manual';
  const name=String(values.name||'').trim();
  const instructions=String(values.instructions||'').trim();
  if(!name||!instructions)throw new Error('Укажите название и инструкции оператора.');
  return {name,instructions,enabled:values.confirmedLaunch===true,mode,language:'auto',style:'balanced',delay_seconds:40,
   schedule:nightAgentSchedule(values.timezone,values.days,values.start,values.end),scopes:nightAgentScope(values.channelId),
   model:values.model===DEFAULT_MODEL?'':String(values.model||''),
   safety:{protect_internal:true,verified_facts:true,block_adult:true,block_prompt_injection:true,scope_only:true}};
 }

 async function loadProfile(){
  if(profile)return profile;
  const response=await fetch(config.profileUrl||'/api/platform/me',{credentials:'same-origin',cache:'no-store'});
  const data=await response.json().catch(()=>null);
  if(!response.ok||!data?.member)throw new Error('Не удалось подтвердить доступ к компании. Обновите кабинет и войдите снова.');
  profile=data.member;
  return profile;
 }
 async function api(path,method='GET',data){
  const current=await loadProfile();
  const response=await fetch(endpoint+path,{method,credentials:'same-origin',cache:'no-store',headers:data?{'Content-Type':'application/json'}:{},...(data?{body:JSON.stringify({...data,expected_tenant_id:current.tenant_id,expected_user_id:current.telegram_id})}:{})});
  const payload=await response.json().catch(()=>({}));
  if(!response.ok||!payload.success){const error=new Error(payload.error||'AI Agents сейчас недоступен.');error.status=response.status;throw error}
  return payload.result;
 }
 async function uploadKnowledge(file,title){
  const current=await loadProfile(),form=new FormData();
  form.append('file',file,file.name);
  form.append('title',title||file.name);
  form.append('expected_tenant_id',current.tenant_id);
  form.append('expected_user_id',current.telegram_id);
  const response=await fetch(endpoint+'knowledge/upload',{method:'POST',credentials:'same-origin',cache:'no-store',body:form});
  const payload=await response.json().catch(()=>({}));
  if(!response.ok||!payload.success)throw new Error(payload.error||'Базу знаний не удалось загрузить.');
  return payload.result;
 }

 function modelValue(){return $('nightAgentModel').value}
 function setupForm(){
  const model=$('nightAgentModel'),channel=$('nightAgentChannel');
  channel.replaceChildren(option('Загрузка подключённых каналов…',''));
  model.replaceChildren(option('Загрузка разрешённых моделей…',''));
  $('nightAgentName').value='Ночная техническая поддержка';
  $('nightAgentInstructions').value=DEFAULT_INSTRUCTIONS;
  $('nightAgentHandoff').value=DEFAULT_HANDOFF;
  $('nightAgentTimezone').value='Asia/Baku';
  $('nightAgentStart').value='22:00';
  $('nightAgentEnd').value='06:00';
  document.querySelectorAll('[name=nightAgentDay]').forEach(node=>{node.checked=Number(node.value)<=4});
  $('nightAgentMode').value='manual';
  $('nightAgentLaunch').checked=false;
  $('nightAgentKnowledgeFile').value='';
  $('nightAgentKnowledgeTitle').value='';
  createdAgent=null;triggerSaved=false;uploadedFileSignature='';
 }
 function fillOptions(){
  const channel=$('nightAgentChannel'),model=$('nightAgentModel');
  const channels=Array.isArray(options?.connect_channels)?options.connect_channels:[];
  channel.replaceChildren(option(channels.length?'Выберите WhatsApp-канал':'Нет подключённых WhatsApp-каналов',''));
  channels.forEach(item=>channel.append(option(String(item.name||item.id||'WhatsApp'),String(item.id||''))));
  const models=(Array.isArray(options?.models)?options.models:[]).map(item=>typeof item==='string'?{id:item,label:item}:{id:String(item?.id??''),label:String(item?.label||item?.id||'Модель')});
  model.replaceChildren();
  if(!models.length)model.append(option('Нет разрешённых моделей — завершите настройку Chat AI',''));
  models.forEach((item,index)=>model.append(option(item.label,item.id||DEFAULT_MODEL,index===0)));
  $('nightAgentExistingKnowledge').textContent=knowledgeSources.length?`Уже доступно источников базы знаний: ${knowledgeSources.length}.`:'В компании пока нет источников базы знаний: загрузите файл на этом шаге.';
 }
 function modeHint(){
  const automatic=$('nightAgentMode').value==='automatic',launch=$('nightAgentLaunch').checked;
  const current=settings?.operating_mode==='active'?'Active Mode уже задан в настройках компании':'Компания остаётся в Test Mode';
  $('nightAgentModeHint').textContent=automatic
   ?`Automatic выбран явно. ${current}; мастер не меняет этот режим и не включает автоотправку.`
   :`${current}. Manual создаёт только черновики, пока его не отправит сотрудник.`;
  $('nightAgentLaunchHint').textContent=launch
   ?'Вы явно подтвердили включение агента. Системные ограничения, права и выбранное расписание всё равно применяются.'
   :'Агент будет сохранён выключенным. Его можно включить позже в обычном редакторе.';
 }
 function scheduleHint(){
  const start=$('nightAgentStart').value,end=$('nightAgentEnd').value,days=selectedDays();
  const suffix=start&&end&&end<start?' следующего дня':'';
  $('nightAgentScheduleHint').textContent=days.length&&start&&end&&start!==end?`Смена: ${start}–${end}${suffix}; дни означают дату начала смены.`:'Выберите хотя бы один день и разные часы.';
 }
 function values(){return {name:$('nightAgentName').value,instructions:$('nightAgentInstructions').value,channelId:$('nightAgentChannel').value,
  timezone:$('nightAgentTimezone').value,days:selectedDays(),start:$('nightAgentStart').value,end:$('nightAgentEnd').value,
  model:modelValue(),mode:$('nightAgentMode').value,confirmedLaunch:$('nightAgentLaunch').checked,handoff:$('nightAgentHandoff').value.trim()}}
 function validate(step){
  const current=values(),file=$('nightAgentKnowledgeFile').files[0];
  if(step===0&&!current.name.trim())throw new Error('Введите название AI-оператора.');
  if(step===1&&!current.channelId)throw new Error('Выберите один подключённый WhatsApp-канал.');
  if(step===2&&!current.timezone.trim())throw new Error('Укажите IANA-часовой пояс компании, например Asia/Baku.');
  if(step===3)nightAgentSchedule(current.timezone,current.days,current.start,current.end);
  if(step===4&&!modelValue())throw new Error('Выберите модель из разрешённого списка.');
  if(step===5&&!current.instructions.trim())throw new Error('Добавьте инструкции компании для ночной поддержки.');
  if(step===6){
   if(!file&&!knowledgeSources.length)throw new Error('Загрузите TXT, PDF или DOCX: у компании ещё нет базы знаний.');
   if(file&&(!/\.(txt|pdf|docx)$/i.test(file.name)||file.size>5*1024*1024))throw new Error('Поддерживаются TXT, PDF или DOCX до 5 МБ.');
  }
  if(step===7&&(!current.handoff||current.handoff.length>200))throw new Error('Укажите до 200 символов условий передачи через символ |.');
  if(step===8&&!['manual','automatic'].includes(current.mode))throw new Error('Выберите Manual или Automatic.');
 }
 function review(){
  const current=values(),file=$('nightAgentKnowledgeFile').files[0],model=$('nightAgentModel').selectedOptions[0]?.textContent||'—';
  const schedule=`${current.days.map(day=>['Пн','Вт','Ср','Чт','Пт','Сб','Вс'][day]).join(', ')} · ${current.start}–${current.end}${current.end<current.start?' следующего дня':''} · ${current.timezone}`;
  const rows=[['Оператор',current.name||'—'],['WhatsApp', $('nightAgentChannel').selectedOptions[0]?.textContent||'—'],['График',schedule],['Модель',model],['Режим',current.mode==='automatic'?'Automatic (выбран явно)':'Manual'],['База знаний',file?file.name:`используется существующая: ${knowledgeSources.length} источник(ов)`],['Передача человеку',current.handoff||'—'],['Запуск',current.confirmedLaunch?'агент будет включён по явному подтверждению':'агент будет сохранён выключенным']];
  const list=$('nightAgentReview');list.replaceChildren(...rows.map(([label,value])=>{const row=document.createElement('div'),strong=document.createElement('b'),span=document.createElement('span');strong.textContent=label;span.textContent=value;row.append(strong,span);return row}));
  $('nightAgentCompanyMode').textContent=settings?.operating_mode==='active'
   ?'Active Mode уже включён в настройках компании. Мастер его не меняет; проверьте права и server kill switch.'
   :'Компания остаётся в Test Mode. Мастер не включает Active Mode и не меняет автоотправку.';
 }
 function render(step){
  const bounded=Math.max(0,Math.min(step,STEPS.length-1));
  document.querySelectorAll('[data-night-step]').forEach(node=>node.hidden=Number(node.dataset.nightStep)!==bounded);
  document.querySelectorAll('[data-night-progress]').forEach(node=>{const number=Number(node.dataset.nightProgress);node.classList.toggle('is-current',number===bounded);node.classList.toggle('is-done',number<bounded);node.setAttribute('aria-current',number===bounded?'step':'false')});
  $('nightAgentPrevious').hidden=bounded===0;$('nightAgentNext').hidden=bounded===STEPS.length-1;$('nightAgentCreate').hidden=bounded!==STEPS.length-1;
  $('nightAgentStepTitle').textContent=`${bounded+1}. ${STEPS[bounded]}`;
  if(bounded===3)scheduleHint();if(bounded===8)modeHint();if(bounded===9)review();
  $('nightAgentWizard').dataset.step=String(bounded);
 }
 function currentStep(){return Number($('nightAgentWizard').dataset.step||'0')}
 function close(){const modal=$('nightAgentTemplateModal');modal.hidden=true;document.body.removeAttribute('data-night-agent-template-open');returnFocus?.focus?.();returnFocus=null}
 async function open(){
  returnFocus=document.activeElement;setNotice('Загружаю доступные каналы, модели и базу знаний…');$('nightAgentTemplateModal').hidden=false;document.body.dataset.nightAgentTemplateOpen='true';setupForm();render(0);
  try{await loadProfile();[options,settings,knowledgeSources]=await Promise.all([api('agents/options'),api('agents/settings'),api('knowledge')]);fillOptions();modeHint();setNotice('Шаблон не меняет настройки компании до финального сохранения.','good');$('nightAgentName').focus()}
  catch(error){showError(error.message)}
 }
 async function create(){
  try{
   validate(0);validate(1);validate(2);validate(3);validate(4);validate(5);validate(6);validate(7);validate(8);
   const button=$('nightAgentCreate');button.disabled=true;button.textContent='Сохраняю…';setNotice('Сохраняю стандартную конфигурацию AI Agent…');
   const current=values(),file=$('nightAgentKnowledgeFile').files[0];
   if(!createdAgent){createdAgent=await api('agents','POST',nightAgentPayload(current));setNotice('Агент сохранён. Настраиваю передачу человеку…','good')}
   if(!triggerSaved){await api(`agents/${encodeURIComponent(createdAgent.id)}/triggers`,'POST',{name:'Ночная техподдержка: передать человеку',trigger_type:'phrase',condition:{phrase:current.handoff,target_stage_id:'',task_text:''},actions:['handoff'],enabled:true});triggerSaved=true}
   if(file&&uploadedFileSignature!==fileSignature(file)){await uploadKnowledge(file,$('nightAgentKnowledgeTitle').value.trim()||file.name);uploadedFileSignature=fileSignature(file)}
   const activation=current.confirmedLaunch?'включён по вашему явному подтверждению':'сохранён выключенным';
   setNotice(`Готово: «${createdAgent.name}» ${activation}. Active Mode и автоотправка компании не изменялись.`,`good`);
   button.textContent='Готово';
  }catch(error){showError(error.message);$('nightAgentCreate').disabled=false;$('nightAgentCreate').textContent=createdAgent?'Повторить незавершённый шаг':'Создать оператор'}
 }
 function install(){
  const opener=$('aiNightTemplateOpen');if(!opener||$('nightAgentTemplateModal'))return;
  const modal=document.createElement('div');modal.id='nightAgentTemplateModal';modal.className='ai-agents-modal';modal.hidden=true;
  modal.innerHTML=`<div class="ai-agents-modal-backdrop" data-night-close></div><section class="ai-agents-dialog" role="dialog" aria-modal="true" aria-labelledby="nightAgentTemplateTitle" tabindex="-1"><header class="ai-agents-dialog-head"><div><h2 id="nightAgentTemplateTitle">Шаблон: Ночная техническая поддержка</h2><p id="nightAgentStepTitle" class="sub"></p></div><button type="button" class="outline" data-night-close>Закрыть</button></header><div class="ai-agents-dialog-body"><article class="panel night-agent-template" id="nightAgentWizard" data-step="0"><p class="sub">Мастер сохраняет обычного AI Agent, Connect scope, расписание, knowledge source и handoff-триггер. Новые режимы или хранилища не создаются.</p><ol class="night-agent-progress" aria-label="Шаги настройки">${STEPS.map((step,index)=>`<li data-night-progress="${index}"><span>${index+1}</span>${step}</li>`).join('')}</ol><p class="ai-effective-status" id="nightAgentNotice" role="status" aria-live="polite"></p><section data-night-step="0"><label>Название AI-оператора<input id="nightAgentName" maxlength="120" placeholder="Например, Ночная техническая поддержка"></label><p class="sub">Это имя будет видно сотрудникам в AI Agents и диалогах.</p></section><section data-night-step="1" hidden><label>Подключённый WhatsApp-канал<select id="nightAgentChannel"></select></label><p class="sub">Доступны только активные Connect-каналы этой компании. Канал является обязательной границей tenant/channel isolation.</p></section><section data-night-step="2" hidden><label>Часовой пояс компании<input id="nightAgentTimezone" list="nightAgentTimezoneList" placeholder="Asia/Baku"><datalist id="nightAgentTimezoneList"><option value="Asia/Baku"><option value="Europe/Moscow"><option value="Europe/Istanbul"><option value="UTC"></datalist></label><p class="sub">Используйте IANA-идентификатор. Сервер дополнительно проверит его перед сохранением.</p></section><section data-night-step="3" hidden><fieldset class="ai-weekdays"><legend>Дни начала ночной смены</legend><label><input name="nightAgentDay" type="checkbox" value="0"> Пн</label><label><input name="nightAgentDay" type="checkbox" value="1"> Вт</label><label><input name="nightAgentDay" type="checkbox" value="2"> Ср</label><label><input name="nightAgentDay" type="checkbox" value="3"> Чт</label><label><input name="nightAgentDay" type="checkbox" value="4"> Пт</label><label><input name="nightAgentDay" type="checkbox" value="5"> Сб</label><label><input name="nightAgentDay" type="checkbox" value="6"> Вс</label></fieldset><div class="grid"><label>Начало<input id="nightAgentStart" type="time"></label><label>Окончание<input id="nightAgentEnd" type="time"></label></div><p class="ai-effective-status" id="nightAgentScheduleHint"></p><p class="sub">Например, Пн 22:00–06:00 означает, что смена продолжится во вторник до 06:00.</p></section><section data-night-step="4" hidden><label>Разрешённая модель<select id="nightAgentModel"></select></label><p class="sub">Список приходит из текущих Chat AI/платформенных разрешений этой компании.</p></section><section data-night-step="5" hidden><label>Инструкции компании для ночной поддержки<textarea id="nightAgentInstructions" rows="12" maxlength="20000"></textarea></label><p class="sub">Они сохранятся как инструкции этого оператора; общие настройки компании не будут переписаны.</p></section><section data-night-step="6" hidden><label>База знаний: TXT, PDF или DOCX до 5 МБ<input id="nightAgentKnowledgeFile" type="file" accept=".txt,.pdf,.docx"></label><label>Название источника (необязательно)<input id="nightAgentKnowledgeTitle" maxlength="180" placeholder="Например, Регламент ночной поддержки"></label><p class="ai-effective-status" id="nightAgentExistingKnowledge"></p><p class="sub">Источник хранится в существующей базе знаний компании. Если источник уже есть, загрузка нового файла необязательна.</p></section><section data-night-step="7" hidden><label>Условия передачи человеку<textarea id="nightAgentHandoff" rows="4" maxlength="200" placeholder="оператор | авария | критическая ошибка"></textarea></label><p class="sub">Разделяйте ключевые фразы символом <b>|</b>. Мастер создаст разрешённый handoff-триггер: при совпадении диалог перейдёт человеку и нерешённое обращение будет зафиксировано в текущем контуре поддержки.</p></section><section data-night-step="8" hidden><label>Режим ответа<select id="nightAgentMode"><option value="manual">Manual — только черновики</option><option value="automatic">Automatic — самостоятельная отправка</option></select></label><p class="ai-effective-status" id="nightAgentModeHint"></p><label class="ai-switch-row"><input id="nightAgentLaunch" type="checkbox"><span><b>Явно подтверждаю включение этого агента</b><small>Без отметки шаблон будет сохранён выключенным. Мастер никогда сам не переключает company Active Mode или автоотправку.</small></span></label><p class="ai-effective-status" id="nightAgentLaunchHint"></p></section><section data-night-step="9" hidden><h3>Финальная проверка готовности</h3><div class="night-agent-review" id="nightAgentReview"></div><p class="ai-effective-status" id="nightAgentCompanyMode"></p><p class="sub">После сохранения новые сообщения будут обрабатываться только по выбранному расписанию, каналу и существующим server/company ограничениям. Старые сообщения не запускаются мастером.</p></section><footer class="ai-editor-footer"><button class="outline" type="button" id="nightAgentPrevious">Назад</button><button class="primary" type="button" id="nightAgentNext">Продолжить</button><button class="primary" type="button" id="nightAgentCreate" hidden>Создать оператор</button></footer></article></div></section>`;
  const style=document.createElement('style');style.textContent=`.night-agent-template{margin:0!important}.night-agent-template label{display:grid;gap:7px;margin:0 0 15px;color:var(--ink);font-weight:700}.night-agent-template input:not([type=checkbox]),.night-agent-template select,.night-agent-template textarea{box-sizing:border-box;width:100%;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:10px 11px;font:inherit}.night-agent-template textarea{resize:vertical}.night-agent-template [data-night-step]>h3{margin-top:0}.night-agent-progress{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 16px;padding:0;list-style:none}.night-agent-progress li{display:flex;align-items:center;gap:5px;padding:4px 7px;border-radius:999px;background:var(--paper);color:var(--muted);font-size:11px;font-weight:700}.night-agent-progress li span{display:grid;place-items:center;width:17px;height:17px;border-radius:50%;background:var(--line);font-size:10px}.night-agent-progress li.is-current{background:#dff7e8;color:#137442}.night-agent-progress li.is-current span,.night-agent-progress li.is-done span{background:var(--brand);color:white}.night-agent-progress li.is-done{color:var(--ink)}.night-agent-review{display:grid;gap:8px}.night-agent-review>div{display:grid;grid-template-columns:minmax(120px,.35fr) 1fr;gap:12px;padding:10px 0;border-bottom:1px solid var(--line);font-size:13px}.night-agent-review b{color:var(--muted)}.night-agent-review span{overflow-wrap:anywhere}#nightAgentNotice[data-state=warn]{color:#a4231a}#nightAgentNotice[data-state=good]{color:#137442}@media(max-width:560px){.night-agent-review>div{grid-template-columns:1fr;gap:3px}.night-agent-progress{gap:4px}.night-agent-progress li{font-size:10px}}`;
  document.head.append(style);document.body.append(modal);
  opener.addEventListener('click',()=>{if(!$('aiAgentsModal')?.hidden)$('aiAgentsModalClose')?.click();open()});
  const overviewActions=document.querySelector('#aiAgentsOverview .ai-overview-actions');
  if(overviewActions){const quick=document.createElement('button');quick.type='button';quick.className='outline';quick.textContent='Ночной оператор';quick.addEventListener('click',open);overviewActions.insertBefore(quick,overviewActions.querySelector('[data-ai-overview-diagnostics]'))}
  modal.querySelectorAll('[data-night-close]').forEach(node=>node.addEventListener('click',close));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!modal.hidden)close()});
  $('nightAgentPrevious').addEventListener('click',()=>render(currentStep()-1));
  $('nightAgentNext').addEventListener('click',()=>{try{validate(currentStep());setNotice('');render(currentStep()+1)}catch(error){showError(error.message)}});
  $('nightAgentCreate').addEventListener('click',create);
  $('nightAgentStart').addEventListener('change',scheduleHint);$('nightAgentEnd').addEventListener('change',scheduleHint);document.querySelectorAll('[name=nightAgentDay]').forEach(node=>node.addEventListener('change',scheduleHint));
  $('nightAgentMode').addEventListener('change',modeHint);$('nightAgentLaunch').addEventListener('change',modeHint);
 }
 install();
 window.BEINTASKBOT_NIGHT_AGENT_TEMPLATE={nightAgentSchedule,nightAgentScope,nightAgentPayload};
})();
