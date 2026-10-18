/* Guided setup for the existing tenant-scoped AI Agents API. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id);
 const config=window.TENANT_AI_AGENTS_CONFIG||{};
 const endpoint=config.endpoint||'/api/platform/ai/';
 const DEFAULT_MODEL='__platform_default__';
 const DEFAULT_HANDOFF='оператор | менеджер | человек | авария | критическая ошибка | не работает';
 const DEFAULT_INSTRUCTIONS=`Вы — вежливый AI-оператор технической поддержки.\n\nРаботайте только в выбранные дни и часы. Поздоровайтесь в первом ответе активной сессии и отвечайте на языке клиента. Отвечайте только на основе доступной базы знаний и этих инструкций. Если данных недостаточно, задайте короткий уточняющий вопрос или честно скажите, что решение неизвестно.\n\nНе выдумывайте решения, инструкции, сроки, цены, статусы или результаты действий. Для сложных, аварийных или нерешённых проблем сообщайте клиенту, что обращение передаётся человеку, и не обещайте невозможного. Условия передачи ниже создадут существующий handoff-триггер: он фиксирует нерешённое обращение в текущем контуре поддержки.`;
 const STEPS=['Название','Подключения','Часовой пояс','Расписание','Модель','Инструкции','База знаний','Передача человеку','Режим','Проверка'];
 const RECOVERY_PREFIX='beintaskbot.night-agent-template.v1';
 const ERROR_COPY={
  ru:{name:'Проверьте название AI-оператора.',instructions:'Проверьте инструкции компании.',timezone:'Проверьте часовой пояс компании.',schedule:'Проверьте дни и часы смены.',model:'Выберите модель из разрешённого списка.',channel:'Выбранный WhatsApp-канал недоступен для этой компании. Обновите список и выберите активный канал.',handoff:'Проверьте условия передачи человеку.',knowledge_file:'Проверьте файл базы знаний и повторите загрузку.',permissions:'Недостаточно прав для создания AI-оператора в этой компании.',activation:'Агент сохранён, но его включение не подтверждено сервером. Проверьте статус в редакторе AI Agents.',configuration:'Не удалось сохранить настройки мастера. Повторите незавершённый шаг.'},
  az:{name:'AI operatorun adını yoxlayın.',instructions:'Şirkət təlimatlarını yoxlayın.',timezone:'Şirkətin saat qurşağını yoxlayın.',schedule:'Gecə növbəsinin gün və saatlarını yoxlayın.',model:'İcazəli siyahıdan model seçin.',channel:'Seçilmiş WhatsApp kanalı bu şirkət üçün əlçatan deyil. Siyahını yeniləyin və aktiv kanalı seçin.',handoff:'Əməkdaşa ötürmə şərtlərini yoxlayın.',knowledge_file:'Bilik bazası faylını yoxlayın və yükləməni təkrarlayın.',permissions:'Bu şirkətdə AI operator yaratmaq üçün icazə yoxdur.',activation:'Agent saxlanıldı, lakin aktivləşdirmə server tərəfindən təsdiqlənmədi. AI Agents redaktorunda statusu yoxlayın.',configuration:'Ustanın parametrlərini saxlamaq alınmadı. Tamamlanmamış addımı təkrarlayın.'},
  en:{name:'Check the AI operator name.',instructions:'Check the company instructions.',timezone:'Check the company time zone.',schedule:'Check the working days and hours.',model:'Select a model from the allowed list.',channel:'The selected WhatsApp channel is unavailable for this company. Refresh the list and choose an active channel.',handoff:'Check the handoff conditions.',knowledge_file:'Check the knowledge-base file and retry the upload.',permissions:'You do not have permission to create an AI operator for this company.',activation:'The agent was saved, but the server did not confirm activation. Check its status in the AI Agents editor.',configuration:'The wizard settings could not be saved. Retry the unfinished step.'}
 };
 let profileStamp='',epoch=0,flow=0,saving=false,completed=false,templateId='technical-support',channelRows=[],frozen=null,agentRole='primary',entryStage=0,dirty=false,recoveredDraft=null;
 document.addEventListener('tenant-profile',event=>{const stamp=JSON.stringify({member:event.detail?.member,allowed:event.detail?.capabilities?.modules?.ai_agents});if(stamp===profileStamp)return;profileStamp=stamp;epoch++;profile=event.detail?.member||null;createdAgent=null;triggerSaved=false;uploadedFileSignature='';setupKey='';channelRows=[];frozen=null;if($('nightAgentTemplateModal'))close(true)});
 let profile=null,options=null,settings=null,knowledgeSources=[],returnFocus=null,createdAgent=null,triggerSaved=false,uploadedFileSignature='',setupKey='';

 function option(label,value,selected=false){const node=document.createElement('option');node.value=value;node.textContent=label;node.selected=selected;return node}
 function setNotice(message,state=''){const node=$('nightAgentNotice');if(!node)return;node.textContent=message||'';node.dataset.state=state}
 function showError(message){setNotice(message,'warn')}
 function selectedDays(){return [...document.querySelectorAll('[name=nightAgentDay]:checked')].map(node=>Number(node.value)).sort((a,b)=>a-b)}
 function fileSignature(file){return file?`${file.name}:${file.size}:${file.lastModified}`:''}
 function errorLanguage(){const language=window.tenantI18n?.language||document.documentElement.lang||'ru';return language==='az'||language==='en'?language:'ru'}
 function wizardError(error){
  if(!error?.field)return error?.message||ERROR_COPY[errorLanguage()].configuration;
  return ERROR_COPY[errorLanguage()][error.field]||ERROR_COPY[errorLanguage()].configuration;
 }
 function recoveryStorageKey(){return profile?.tenant_id&&profile?.telegram_id?`${RECOVERY_PREFIX}.${profile.tenant_id}.${profile.telegram_id}.${templateId}`:''}
 function newSetupKey(){
  const random=globalThis.crypto?.randomUUID?.().replaceAll('-','')||`${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  return `night-${Date.now().toString(36)}-${random}`.slice(0,120);
 }
 function restoreProgress(){
  const key=recoveryStorageKey();if(!key)return false;
  try{
   const legacy=templateId==='technical-support'?localStorage.getItem(key.replace(/\.technical-support$/,'')):null;
   const blankKey=profile?.tenant_id&&profile?.telegram_id?`${RECOVERY_PREFIX}.${profile.tenant_id}.${profile.telegram_id}.blank`:'';
   const saved=JSON.parse(localStorage.getItem(key)||localStorage.getItem(blankKey)||legacy||'null');
   if(saved&&/^[A-Za-z0-9][A-Za-z0-9_-]{11,119}$/.test(saved.setup_key||'')&&(!saved.template_id||['blank','technical-support'].includes(saved.template_id))){
    templateId=saved.template_id==='blank'?'blank':'technical-support';setupKey=saved.setup_key;createdAgent=saved.agent||null;triggerSaved=saved.trigger_saved===true;uploadedFileSignature=String(saved.uploaded_file_signature||'');frozen=saved.frozen||null;recoveredDraft=saved.draft||null;agentRole=saved.agent_role==='expert'?'expert':'primary';return true;
   }
  }catch(_error){}
  setupKey=newSetupKey();return false;
 }

 function saveProgress(){
  const key=recoveryStorageKey();if(!key||!setupKey)return;
  try{localStorage.setItem(key,JSON.stringify({setup_key:setupKey,template_id:templateId,agent_role:agentRole,draft:values(),agent:createdAgent,trigger_saved:triggerSaved,uploaded_file_signature:uploadedFileSignature,frozen}));}
  catch(_error){throw new Error('Не удалось сохранить ключ повтора в браузере. Разрешите локальное хранилище перед созданием агента.');}
 }

 function clearProgress(){const key=recoveryStorageKey();if(key)try{{localStorage.removeItem(key);if(templateId==='technical-support')localStorage.removeItem(key.replace(/\.technical-support$/,''));}}catch(_error){}}
 function knowledgeSetupKey(file){
  let hash=2166136261;for(const character of fileSignature(file)){hash=Math.imul(hash^character.charCodeAt(0),16777619)}
  return `${setupKey}-kb-${(hash>>>0).toString(36)}`;
 }

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
  const role=values.role==='expert'?'expert':'primary';
  const payload={name,instructions,enabled:values.confirmedLaunch===true,mode:role==='expert'?'manual':mode,language:'auto',style:'balanced',delay_seconds:40,
   schedule:values.scheduleEnabled===false?{}:nightAgentSchedule(values.timezone,values.days,values.start,values.end),scopes:values.scopes||nightAgentScope(values.channelId),
   model:values.model===DEFAULT_MODEL?'':String(values.model||''),role,expert_policy:role==='expert'?(values.expert_policy||{internal:true,allowed_primary_ids:[]}):{},
   safety:{protect_internal:true,verified_facts:true,block_adult:true,block_prompt_injection:true,scope_only:true}};
  if(values.setupKey)payload.setup_key=String(values.setupKey);
  return payload;
 }

 async function loadProfile(){
  if(profile)return profile;
  const response=await fetch(config.profileUrl||'/api/platform/me',{credentials:'same-origin',cache:'no-store'});
  const data=await response.json().catch(()=>null);
  if(!response.ok||!data?.member)throw new Error('Не удалось подтвердить доступ к компании. Обновите кабинет и войдите снова.');
  if(profile)return profile;
  profileStamp=JSON.stringify({member:data.member,allowed:data.capabilities?.modules?.ai_agents});profile=data.member;
  return profile;
 }
 async function api(path,method='GET',data){
  const version=epoch,current=await loadProfile();
  const query=method==='GET'?(path.includes('?')?'&':'?')+new URLSearchParams({expected_tenant_id:current.tenant_id,expected_user_id:current.telegram_id}):'';
  const response=await fetch(endpoint+path+query,{method,credentials:'same-origin',cache:'no-store',headers:data?{'Content-Type':'application/json'}:{},...(data?{body:JSON.stringify({...data,expected_tenant_id:current.tenant_id,expected_user_id:current.telegram_id})}:{})});
  const payload=await response.json().catch(()=>({}));
  if(typeof version!=='undefined'&&version!==epoch)throw new Error('Компания изменилась. Откройте мастер заново.');
  if(!response.ok||!payload.success){const error=new Error(payload.error||'AI Agents сейчас недоступен.');error.status=response.status;error.code=payload.code;error.field=payload.field;throw error}
  return payload.result;
 }
 async function uploadKnowledge(file,title){
  const version=epoch,current=await loadProfile(),form=new FormData();
  form.append('file',file,file.name);
  form.append('title',title||file.name);
  form.append('agent_id',createdAgent.id);
  form.append('setup_key',knowledgeSetupKey(file));
  form.append('expected_tenant_id',current.tenant_id);
  form.append('expected_user_id',current.telegram_id);
  const response=await fetch(endpoint+'knowledge/upload',{method:'POST',credentials:'same-origin',cache:'no-store',body:form});
  const payload=await response.json().catch(()=>({}));
  if(typeof version!=='undefined'&&version!==epoch)throw new Error('Компания изменилась. Откройте мастер заново.');
  if(!response.ok||!payload.success){const error=new Error(payload.error||'Базу знаний не удалось загрузить.');error.status=response.status;error.code=payload.code;error.field=payload.field;throw error}
  return payload.result;
 }

 function modelValue(){return $('nightAgentModel').value}
 function setupForm(){
  const model=$('nightAgentModel');channelRows=[];options=null;settings=null;knowledgeSources=[];$('nightAgentChannelChoices').replaceChildren();
  model.replaceChildren(option('Загрузка разрешённых моделей…',''));
  $('nightAgentName').value=templateId==='blank'?'':'Оператор технической поддержки';
  $('nightAgentInstructions').value=templateId==='blank'?'':DEFAULT_INSTRUCTIONS;
  $('nightAgentHandoff').value=templateId==='blank'?'':DEFAULT_HANDOFF;
  $('nightAgentTimezone').value='Asia/Baku';
  $('nightAgentStart').value='09:00';
  $('nightAgentEnd').value='18:00';
  document.querySelectorAll('[name=nightAgentDay]').forEach(node=>{node.checked=Number(node.value)<=4});
  $('nightAgentMode').value='manual';
  $('nightAgentLaunch').checked=false;
  $('nightAgentKnowledgeFile').value='';
  $('nightAgentKnowledgeTitle').value='';
  createdAgent=null;triggerSaved=false;uploadedFileSignature='';setupKey=newSetupKey();frozen=null;recoveredDraft=null;completed=false;dirty=false;
  $('nightAgentScheduleEnabled').checked=templateId!=='blank';$('nightAgentAfterCreate').hidden=true;
  document.querySelectorAll('#nightAgentWizard input,#nightAgentWizard select,#nightAgentWizard textarea').forEach(node=>node.disabled=false);
  $('nightAgentCreate').disabled=false;$('nightAgentCreate').textContent='Создать агента';
  $('nightAgentTemplateTitle').textContent=templateId==='blank'?'Создать агента с нуля':'Оператор технической поддержки';
 }
 function fillOptions(){
  const model=$('nightAgentModel');renderChannels();
  const models=(Array.isArray(options?.models)?options.models:[]).map(item=>typeof item==='string'?{id:item,label:item}:{id:String(item?.id??''),label:String(item?.label||item?.id||'Модель')});
  model.replaceChildren();
  if(!models.length)model.append(option('Нет разрешённых моделей — завершите настройку Chat AI',''));
  models.forEach((item,index)=>model.append(option(item.label,item.id||DEFAULT_MODEL,index===0)));
  $('nightAgentExistingKnowledge').textContent=knowledgeSources.length?`Уже доступно источников базы знаний: ${knowledgeSources.length}.`:'В компании пока нет источников базы знаний: загрузите файл на этом шаге.';
 }
 function hydrateRecoveredAgent(){
  const agent=frozen?.payload||createdAgent||recoveredDraft;if(!agent)return;const schedule=agent.schedule||{},scopes=agent.scopes||[];
  $('nightAgentName').value=agent.name||$('nightAgentName').value;
  $('nightAgentInstructions').value=agent.instructions||$('nightAgentInstructions').value;
  $('nightAgentTimezone').value=schedule.timezone||$('nightAgentTimezone').value;
  $('nightAgentStart').value=schedule.start||$('nightAgentStart').value;$('nightAgentEnd').value=schedule.end||$('nightAgentEnd').value;
  document.querySelectorAll('[name=nightAgentDay]').forEach(node=>{node.checked=(schedule.days||[]).includes(Number(node.value))});
  $('nightAgentScheduleEnabled').checked=Object.keys(schedule).length>0;
  document.querySelectorAll('[name=agentCreationChannel]').forEach(box=>box.checked=scopes.some(scope=>scope.provider===box.dataset.provider&&String(scope.channel_id)===box.dataset.channel)||(agent.channelIds||[]).includes(box.value));
  const kommo=scopes.find(scope=>scope.provider==='kommo');if(kommo){$('nightAgentPipeline').value=kommo.pipeline_id;renderKommoStages();$('nightAgentStage').value=kommo.stage_id;}
  syncChannels();
  const savedModel=agent.model||DEFAULT_MODEL;if(![...$('nightAgentModel').options].some(option=>option.value===savedModel))$('nightAgentModel').append(option((agent.model||'Default')+' (сохранено)',savedModel));$('nightAgentModel').value=savedModel;
  $('nightAgentMode').value=agentRole==='expert'?'manual':agent.mode==='automatic'?'automatic':'manual';$('nightAgentLaunch').checked=false;
  if(frozen?.form?.handoff!==undefined)$('nightAgentHandoff').value=frozen.form.handoff;
  if(frozen)lockDraft();
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
  const enabled=$('nightAgentScheduleEnabled').checked;$('nightAgentScheduleFields').hidden=!enabled;
  const start=$('nightAgentStart').value,end=$('nightAgentEnd').value,days=selectedDays();
  if(!enabled){$('nightAgentScheduleHint').textContent='Без ограничения по расписанию. Агент остаётся выключенным до явного включения.';return;}
  const suffix=start&&end&&end<start?' следующего дня':'';
  $('nightAgentScheduleHint').textContent=days.length&&start&&end&&start!==end?`Смена: ${start}–${end}${suffix}; дни означают дату начала смены.`:'Выберите хотя бы один день и разные часы.';
 }
 function values(){return {name:$('nightAgentName').value,instructions:$('nightAgentInstructions').value,channelIds:selectedChannels(),scheduleEnabled:$('nightAgentScheduleEnabled').checked,
  timezone:$('nightAgentTimezone').value,days:selectedDays(),start:$('nightAgentStart').value,end:$('nightAgentEnd').value,
  model:modelValue(),mode:$('nightAgentMode').value,confirmedLaunch:$('nightAgentLaunch').checked,handoff:$('nightAgentHandoff').value.trim(),setupKey:setupKey}}
 function validate(step){
  const current=values(),file=$('nightAgentKnowledgeFile').files[0];
  if(step===0&&!current.name.trim())throw new Error('Введите название AI-оператора.');
  if(step===1&&agentRole==='primary')selectedScopes();
  if(step===2&&!current.timezone.trim())throw new Error('Укажите IANA-часовой пояс компании, например Asia/Baku.');
  if(step===3&&current.scheduleEnabled)nightAgentSchedule(current.timezone,current.days,current.start,current.end);
  if(step===4&&!modelValue())throw new Error('Выберите модель из разрешённого списка.');
  if(step===5&&!current.instructions.trim())throw new Error('Добавьте инструкции компании для технической поддержки.');
  if(step===6){
   if(templateId!=='blank'&&!file&&!knowledgeSources.length&&!uploadedFileSignature)throw new Error('Загрузите TXT, PDF или DOCX: у компании ещё нет базы знаний.');
   if(file&&(!/\.(txt|pdf|docx)$/i.test(file.name)||file.size>5*1024*1024))throw new Error('Поддерживаются TXT, PDF или DOCX до 5 МБ.');
  }
  if(step===7&&((templateId!=='blank'&&!current.handoff)||current.handoff.length>200))throw new Error('Укажите до 200 символов условий передачи через символ |.');
  if(step===8&&(agentRole==='expert'?current.mode!=='manual':!['manual','automatic'].includes(current.mode)))throw new Error('Выберите Manual или Automatic.');
 }
 function review(){
  const current=values(),file=$('nightAgentKnowledgeFile').files[0],model=$('nightAgentModel').selectedOptions[0]?.textContent||'—';
  const t=value=>window.tenantNightL10n?.text(value)||value;
  const schedule=current.scheduleEnabled?`${current.days.map(day=>t(['Пн','Вт','Ср','Чт','Пт','Сб','Вс'][day])).join(', ')} · ${current.start}–${current.end}${current.end<current.start?' следующего дня':''} · ${current.timezone}`:'Без ограничения по расписанию';
  const rows=[['Тип',agentRole==='expert'?'Внутренний':'Основной'],['Оператор',current.name||'—'],['Подключения',agentRole==='expert'?'Внутренний агент не использует внешние каналы':selectedChannels().map(id=>window.tenantAgentCatalog.connectionName(channelRows.find(row=>row.id===id))).join('; ')||'—'],['График',schedule],['Модель',model],['Режим',current.mode==='automatic'?'Automatic (выбран явно)':'Manual'],['База знаний',file?file.name:uploadedFileSignature?'Файл уже загружен':knowledgeSources.length?`используется существующая: ${knowledgeSources.length} источник(ов)`:'Без базы знаний — можно добавить позже'],['Передача человеку',agentRole==='expert'?'Не применяется к внутреннему агенту':current.handoff||'Дополнительный триггер не задан'],['Запуск',current.confirmedLaunch?'агент будет включён по явному подтверждению':'агент будет сохранён выключенным']];
  const list=$('nightAgentReview');list.replaceChildren(...rows.map(([label,value])=>{const row=document.createElement('div'),strong=document.createElement('b'),span=document.createElement('span');strong.textContent=label;span.textContent=t(value);span.setAttribute('data-user-content','');row.append(strong,span);return row}));
  $('nightAgentCompanyMode').textContent=settings?.operating_mode==='active'
   ?'Active Mode уже включён в настройках компании. Мастер его не меняет; проверьте права и server kill switch.'
   :'Компания остаётся в Test Mode. Мастер не включает Active Mode и не меняет автоотправку.';
 }
 function render(step,direction=1){
  const bounded=Math.max(0,Math.min(step,STEPS.length-1));
  const visible=agentRole==='expert'&&bounded===1?(direction>0?2:0):bounded;
  $('nightAgentEntry').hidden=true;$('nightAgentProgress').hidden=false;document.querySelectorAll('[data-night-step]').forEach(node=>node.hidden=Number(node.dataset.nightStep)!==visible);
  document.querySelectorAll('[data-night-progress]').forEach(node=>{const number=Number(node.dataset.nightProgress);node.classList.toggle('is-current',number===visible);node.classList.toggle('is-done',number<visible);node.setAttribute('aria-current',number===visible?'step':'false')});
  $('nightAgentPrevious').hidden=visible===0;$('nightAgentNext').hidden=visible===STEPS.length-1;$('nightAgentNext').textContent='Продолжить';$('nightAgentCreate').hidden=visible!==STEPS.length-1;
  $('nightAgentStepTitle').textContent=`${visible+1}. ${STEPS[visible]}`;
  const automatic=[...$('nightAgentMode').options].find(option=>option.value==='automatic');if(automatic)automatic.disabled=agentRole==='expert';if(agentRole==='expert')$('nightAgentMode').value='manual';
  if(visible===3)scheduleHint();if(visible===8)modeHint();if(visible===9)review();
  $('nightAgentWizard').dataset.step=String(visible);
 }
 function currentStep(){return Number($('nightAgentWizard').dataset.step||'0')}
 function renderEntry(stage){entryStage=stage;$('nightAgentEntry').hidden=false;$('nightAgentProgress').hidden=true;document.querySelectorAll('[data-night-step]').forEach(node=>node.hidden=true);document.querySelectorAll('[data-night-entry]').forEach(node=>node.hidden=Number(node.dataset.nightEntry)!==stage);$('nightAgentPrevious').hidden=stage===0;$('nightAgentNext').hidden=false;$('nightAgentCreate').hidden=true;$('nightAgentNext').textContent=stage===1?'Перейти к настройкам':'Продолжить';$('nightAgentStepTitle').textContent=stage===0?'1. Тип агента':'2. Способ создания';}
 function close(force=false){if(!force&&dirty&&!completed&&!confirm('Закрыть мастер? Несохранённые настройки останутся в черновике этого сотрудника.'))return;flow++;saving=false;const modal=$('nightAgentTemplateModal');modal.hidden=true;document.body.removeAttribute('data-night-agent-template-open');returnFocus?.focus?.();returnFocus=null}
 async function open(params={}){
  if(params.templateId&&!['blank','technical-support'].includes(params.templateId))throw new Error('Шаблон недоступен.');
  templateId=params.templateId==='blank'?'blank':'technical-support';agentRole='primary';entryStage=0;flow++;const run=flow,version=epoch;
  returnFocus=document.activeElement;$('nightAgentTemplateModal').hidden=false;$('nightAgentTemplateModal').querySelector('[role=dialog]').focus();document.body.dataset.nightAgentTemplateOpen='true';setupForm();document.querySelector(`[name=nightAgentTemplateChoice][value="${templateId}"]`).checked=true;renderEntry(0);setNotice('Загружаю доступные каналы, модели и базу знаний…');
  $('nightAgentNext').disabled=true;
  try{
   await loadProfile();const loaded=await Promise.all([api('agents/options?cached=1'),api('agents/settings'),api('knowledge'),api('channels')]);
   if(run!==flow||version!==epoch)return;
   [options,settings,knowledgeSources]=loaded;channelRows=window.tenantAgentCatalog.parse(loaded[3]);
   knowledgeSources=knowledgeSources.filter(item=>!item.agent_id&&item.active!==false);fillOptions();const recovered=restoreProgress();
   if(!recovered&&(params.channelIds||[]).length){
    const chosen=new Set(params.channelIds);
    if([...chosen].some(id=>!channelRows.some(row=>row.id===id&&row.selectable)))throw new Error('Выбранное подключение больше недоступно. Обновите каталог.');
    document.querySelectorAll('[name=agentCreationChannel]').forEach(box=>{box.checked=chosen.has(box.value)});syncChannels();
   }
   if(recovered){hydrateRecoveredAgent();entryStage=2;render(0);setNotice('Продолжаем незавершённое создание. Повторная отправка не создаст дубликат.','good')}else setNotice('Мастер не меняет общие настройки компании.','good');
   modeHint();scheduleHint();$('nightAgentNext').disabled=false;$('nightAgentEntryRole').focus();
  }catch(error){if(run===flow&&version===epoch)showError(error.message)}
 }

 async function create(){
  if(saving||completed)return;const run=flow,version=epoch;
  try{
   const file=$('nightAgentKnowledgeFile').files[0];
   if(!frozen){
    for(let step=0;step<9;step++)if(!(agentRole==='expert'&&step===1))validate(step);
    if(!setupKey)setupKey=newSetupKey();
    const current=values();frozen={payload:{...nightAgentPayload({...current,mode:agentRole==='expert'?'manual':current.mode,scopes:selectedScopes(),role:agentRole,expert_policy:agentRole==='expert'?{internal:true,allowed_primary_ids:[]}:{}}),enabled:false},form:current,knowledge:file?{signature:fileSignature(file),title:$('nightAgentKnowledgeTitle').value.trim()||file.name}:null};
   }
   if(frozen.knowledge&&!uploadedFileSignature&&(!file||fileSignature(file)!==frozen.knowledge.signature))throw new Error('Повторно выберите исходный файл базы знаний. Сохранённый агент не будет создан заново.');
   saveProgress();saving=true;lockDraft();$('nightAgentCreate').disabled=true;$('nightAgentCreate').textContent='Сохраняю…';setNotice('Сохраняю стандартную конфигурацию AI Agent…');
   const activate=$('nightAgentLaunch').checked;
   if(!createdAgent){
    const fresh=window.tenantAgentCatalog.parse(await api('channels'));
    if(run!==flow||version!==epoch)return;
    const selected=frozen.form.channelIds||frozen.payload.scopes.map(scope=>scope.provider+':'+scope.channel_id);
    window.tenantAgentCatalog.scopes(fresh,selected,frozen.payload.scopes.find(scope=>scope.provider==='kommo')?.pipeline_id,frozen.payload.scopes.find(scope=>scope.provider==='kommo')?.stage_id);
   }
   // Always re-read the original employee-owned record through the existing idempotent POST.
   frozen.agent_requested=true;saveProgress();
   const saved=await api('agents','POST',{...frozen.payload,enabled:false});if(run!==flow||version!==epoch)return;createdAgent=saved;saveProgress();
   if(!sameConfiguration(saved,frozen.payload)){$('nightAgentAfterCreate').hidden=false;throw new Error('Агент изменён вне мастера. Откройте его настройки перед продолжением; автоматическое включение отменено.');}
   if(agentRole==='primary'&&frozen.form.handoff&&!triggerSaved){await api(`agents/${encodeURIComponent(createdAgent.id)}/triggers`,'POST',{name:'Техническая поддержка: передать человеку',trigger_type:'phrase',condition:{phrase:frozen.form.handoff,target_stage_id:'',task_text:''},actions:['handoff'],enabled:true,setup_key:`${setupKey}-handoff`});if(run!==flow||version!==epoch)return;triggerSaved=true;saveProgress()}
   if(frozen.knowledge&&!uploadedFileSignature){await uploadKnowledge(file,frozen.knowledge.title);if(run!==flow||version!==epoch)return;uploadedFileSignature=frozen.knowledge.signature;saveProgress()}
   if(activate&&!createdAgent.enabled){const activated=await api(`agents/${encodeURIComponent(createdAgent.id)}`,'PATCH',{expected_version:createdAgent.version,enabled:true});if(run!==flow||version!==epoch)return;createdAgent=activated;}
   completed=true;clearProgress();setNotice(`Готово: «${createdAgent.name}» ${createdAgent.enabled?'включён по вашему явному подтверждению':'сохранён выключенным'}. Active Mode и автоотправка компании не изменялись.`,'good');
   $('nightAgentCreate').textContent='Готово';$('nightAgentAfterCreate').hidden=false;
   document.dispatchEvent(new CustomEvent('tenant-ai-agent-created',{detail:{agentId:createdAgent.id}}));
  }catch(error){if(run===flow&&version===epoch){if(frozen&&!frozen.agent_requested&&!createdAgent){frozen=null;try{saveProgress()}catch(_error){}unlockDraft();}showError(wizardError(error));$('nightAgentCreate').textContent=createdAgent||frozen?'Повторить незавершённый шаг':'Создать агента';}}
  finally{if(run===flow&&version===epoch){saving=false;$('nightAgentCreate').disabled=completed;}}
 }
 function sameConfiguration(agent,payload){
  const canonical=value=>{
   const schedule=value.schedule||{};
   return JSON.stringify({name:value.name,instructions:value.instructions,model:value.model||'',mode:value.mode,
    language:value.language,style:value.style,delay_seconds:value.delay_seconds,
    schedule:Object.keys(schedule).length?{timezone:schedule.timezone,days:schedule.days,start:schedule.start,end:schedule.end}:{},
    scopes:(value.scopes||[]).map(scope=>({provider:scope.provider||'kommo',channel_id:String(scope.channel_id||''),pipeline_id:String(scope.pipeline_id||''),stage_id:String(scope.stage_id||''),instructions:scope.instructions||'',allow_group_messages:scope.allow_group_messages===true})).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)))});
  };
  return canonical(agent)===canonical(payload);
 }
 function selectedChannels(){return [...document.querySelectorAll('[name=agentCreationChannel]:checked')].map(node=>node.value)}
 function selectedScopes(){return agentRole==='expert'?[]:window.tenantAgentCatalog.scopes(channelRows,selectedChannels(),$('nightAgentPipeline').value,$('nightAgentStage').value)}
 function syncChannels(){const selected=selectedChannels();$('nightAgentKommoFields').hidden=!channelRows.some(row=>selected.includes(row.id)&&row.provider==='kommo');}
 function renderKommoStages(){const pipeline=$('nightAgentPipeline').value,stage=$('nightAgentStage');stage.replaceChildren(option('Выберите этап',''));(options?.stages||[]).filter(row=>String(row.pipeline_id)===pipeline).forEach(row=>stage.append(option(row.name||'#'+row.stage_id,String(row.stage_id))));}
 function renderChannels(){
  const host=$('nightAgentChannelChoices');host.replaceChildren();
  for(const row of channelRows){
   const label=document.createElement('label');label.className='agent-channel-choice';const box=document.createElement('input');box.type='checkbox';box.name='agentCreationChannel';box.value=row.id;box.dataset.provider=row.provider;box.dataset.channel=row.channel_id;box.disabled=!row.selectable;
   const copy=document.createElement('span'),title=document.createElement('b'),detail=document.createElement('small');title.textContent=window.tenantAgentCatalog.connectionName(row);title.dataset.userContent='';
   const state={connected:'Подключено',disconnected:'Не подключено',disabled:'Выключено',inactive:'Неактивно',unavailable:'Недоступно',connecting:'Подключается',pending_qr:'Ожидает QR-код',reconnecting:'Переподключается',reconnect_required:'Требуется переподключение',error:'Ошибка подключения'}[row.connection?.state]||'Состояние не подтверждено';
   detail.textContent=`${window.tenantNightL10n?.text(state)||state} · ${window.tenantNightL10n?.text(row.selectable?'AI поддерживается':'AI недоступен')||(row.selectable?'AI поддерживается':'AI недоступен')}`;
   copy.append(title,detail);label.append(box,copy);box.onchange=syncChannels;host.append(label);
  }
  if(!channelRows.length)host.textContent='Нет доступных поддерживаемых подключений.';
  const pipeline=$('nightAgentPipeline');pipeline.replaceChildren(option('Выберите воронку',''));(options?.pipelines||[]).forEach(row=>pipeline.append(option(row.name||'#'+row.pipeline_id,String(row.pipeline_id))));renderKommoStages();syncChannels();
 }
 function unlockDraft(){document.querySelectorAll('#nightAgentWizard input,#nightAgentWizard select,#nightAgentWizard textarea').forEach(node=>node.disabled=node.name==='agentCreationChannel'&&!channelRows.find(row=>row.id===node.value)?.selectable);}
 function lockDraft(){
  document.querySelectorAll('#nightAgentWizard input,#nightAgentWizard select,#nightAgentWizard textarea').forEach(node=>{if(!['nightAgentKnowledgeFile','nightAgentLaunch'].includes(node.id))node.disabled=true;});
  if(frozen&&!frozen.knowledge)$('nightAgentKnowledgeFile').disabled=true;
 }

 function install(){
  const opener=$('aiNightTemplateOpen');if($('nightAgentTemplateModal'))return;
  const modal=document.createElement('div');modal.id='nightAgentTemplateModal';modal.className='ai-agents-modal';modal.hidden=true;
  modal.innerHTML=`<div class="ai-agents-modal-backdrop" data-night-close></div><section class="ai-agents-dialog" role="dialog" aria-modal="true" aria-labelledby="nightAgentTemplateTitle" tabindex="-1"><header class="ai-agents-dialog-head"><div><h2 id="nightAgentTemplateTitle">Оператор технической поддержки</h2><p id="nightAgentStepTitle" class="sub"></p></div><button type="button" class="outline" data-night-close>Закрыть</button></header><div class="ai-agents-dialog-body"><article class="panel night-agent-template" id="nightAgentWizard" data-step="0"><p class="sub">Настройте подключения, расписание и правила передачи сотруднику.</p><ol class="night-agent-progress" aria-label="Шаги настройки">${STEPS.map((step,index)=>`<li data-night-progress="${index}"><span>${index+1}</span>${step}</li>`).join('')}</ol><p class="ai-effective-status" id="nightAgentNotice" role="status" aria-live="polite"></p><section data-night-step="0"><label>Название AI-оператора<input id="nightAgentName" maxlength="120" placeholder="Например, Оператор технической поддержки"></label><p class="sub">Это имя будет видно сотрудникам в AI Agents и диалогах.</p></section><section data-night-step="1" hidden><h3>Подключения компании</h3><p class="sub">Выберите конкретные подключения. Доступность AI подтверждается сервером; неподдерживаемые каналы выбрать нельзя.</p><div id="nightAgentChannelChoices"></div><div id="nightAgentKommoFields" hidden><label>Воронка Kommo<select id="nightAgentPipeline"></select></label><label>Этап Kommo<select id="nightAgentStage"></select></label><p class="sub">Kommo требует явного выбора воронки, этапа и канала. Доступ ко всем каналам не выдаётся.</p></div></section><section data-night-step="2" hidden><label>Часовой пояс компании<input id="nightAgentTimezone" list="nightAgentTimezoneList" placeholder="Asia/Baku"><datalist id="nightAgentTimezoneList"><option value="Asia/Baku"><option value="Europe/Moscow"><option value="Europe/Istanbul"><option value="UTC"></datalist></label><p class="sub">Используйте IANA-идентификатор. Сервер дополнительно проверит его перед сохранением.</p></section><section data-night-step="3" hidden><label class="ai-switch-row"><input id="nightAgentScheduleEnabled" type="checkbox"><span>Ограничить работу расписанием</span></label><div id="nightAgentScheduleFields"><fieldset class="ai-weekdays"><legend>Дни начала смены</legend><label><input name="nightAgentDay" type="checkbox" value="0"> Пн</label><label><input name="nightAgentDay" type="checkbox" value="1"> Вт</label><label><input name="nightAgentDay" type="checkbox" value="2"> Ср</label><label><input name="nightAgentDay" type="checkbox" value="3"> Чт</label><label><input name="nightAgentDay" type="checkbox" value="4"> Пт</label><label><input name="nightAgentDay" type="checkbox" value="5"> Сб</label><label><input name="nightAgentDay" type="checkbox" value="6"> Вс</label></fieldset><div class="grid"><label>Начало<input id="nightAgentStart" type="time"></label><label>Окончание<input id="nightAgentEnd" type="time"></label></div></div><p class="ai-effective-status" id="nightAgentScheduleHint"></p><p class="sub">Например, Пн 22:00–06:00 означает, что смена продолжится во вторник до 06:00.</p></section><section data-night-step="4" hidden><label>Разрешённая модель<select id="nightAgentModel"></select></label><p class="sub">Список приходит из текущих Chat AI/платформенных разрешений этой компании.</p></section><section data-night-step="5" hidden><label>Инструкции агента<textarea id="nightAgentInstructions" rows="12" maxlength="20000"></textarea></label><p class="sub">Они сохранятся как инструкции этого оператора; общие настройки компании не будут переписаны.</p></section><section data-night-step="6" hidden><label>База знаний: TXT, PDF или DOCX до 5 МБ<input id="nightAgentKnowledgeFile" type="file" accept=".txt,.pdf,.docx"></label><label>Название источника (необязательно)<input id="nightAgentKnowledgeTitle" maxlength="180" placeholder="Например, Регламент технической поддержки"></label><p class="ai-effective-status" id="nightAgentExistingKnowledge"></p><p class="sub">Новый файл привязывается только к созданному агенту. Общие источники компании остаются доступны; существующие настройки не меняются.</p></section><section data-night-step="7" hidden><label>Условия передачи человеку<textarea id="nightAgentHandoff" rows="4" maxlength="200" placeholder="оператор | авария | критическая ошибка"></textarea></label><p class="sub">Для создания с нуля этот шаг необязателен. Разделяйте ключевые фразы символом <b>|</b>. Мастер создаст разрешённый handoff-триггер: при совпадении диалог перейдёт человеку и нерешённое обращение будет зафиксировано в текущем контуре поддержки.</p></section><section data-night-step="8" hidden><label>Режим ответа<select id="nightAgentMode"><option value="manual">Manual — только черновики</option><option value="automatic">Automatic — самостоятельная отправка</option></select></label><p class="ai-effective-status" id="nightAgentModeHint"></p><label class="ai-switch-row"><input id="nightAgentLaunch" type="checkbox"><span><b>Явно подтверждаю включение этого агента</b><small>Без отметки шаблон будет сохранён выключенным. Мастер никогда сам не переключает company Active Mode или автоотправку.</small></span></label><p class="ai-effective-status" id="nightAgentLaunchHint"></p></section><section data-night-step="9" hidden><h3>Финальная проверка готовности</h3><div class="night-agent-review" id="nightAgentReview"></div><p class="ai-effective-status" id="nightAgentCompanyMode"></p><p class="sub">После сохранения новые сообщения будут обрабатываться только по выбранному расписанию, каналу и существующим server/company ограничениям. Старые сообщения не запускаются мастером.</p></section><footer class="ai-editor-footer"><button class="outline" type="button" id="nightAgentPrevious">Назад</button><button class="primary" type="button" id="nightAgentNext">Продолжить</button><button class="primary" type="button" id="nightAgentCreate" hidden>Создать агента</button></footer><div class="agent-library-after" id="nightAgentAfterCreate" hidden><button type="button" class="outline" id="nightAgentOpenEditor">Настроить знания, условия остановки и триггеры</button></div></article></div></section>`;
  const style=document.createElement('style');style.textContent=`.night-agent-template{margin:0!important}#nightAgentTemplateModal .sub{white-space:normal;overflow:visible}.night-agent-template label{display:grid;gap:7px;margin:0 0 15px;color:var(--ink);font-weight:700}.night-agent-template input:not([type=checkbox]),.night-agent-template select,.night-agent-template textarea{box-sizing:border-box;width:100%;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:10px 11px;font:inherit}.night-agent-template textarea{resize:vertical}.night-agent-template [data-night-step]>h3{margin-top:0}.night-agent-progress{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 16px;padding:0;list-style:none}.night-agent-progress li{display:flex;align-items:center;gap:5px;padding:4px 7px;border-radius:999px;background:var(--paper);color:var(--muted);font-size:11px;font-weight:700}.night-agent-progress li span{display:grid;place-items:center;width:17px;height:17px;border-radius:50%;background:var(--line);font-size:10px}.night-agent-progress li.is-current{background:#dff7e8;color:#137442}.night-agent-progress li.is-current span,.night-agent-progress li.is-done span{background:var(--brand);color:white}.night-agent-progress li.is-done{color:var(--ink)}.night-agent-review{display:grid;gap:8px}.night-agent-review>div{display:grid;grid-template-columns:minmax(120px,.35fr) 1fr;gap:12px;padding:10px 0;border-bottom:1px solid var(--line);font-size:13px}.night-agent-review b{color:var(--muted)}.night-agent-review span{overflow-wrap:anywhere}#nightAgentNotice[data-state=warn]{color:#a4231a}#nightAgentNotice[data-state=good]{color:#137442}@media(max-width:560px){.night-agent-review>div{grid-template-columns:1fr;gap:3px}.night-agent-progress{gap:4px}.night-agent-progress li{font-size:10px}}`;
  document.head.append(style);document.body.append(modal);
  const entry=document.createElement('section');entry.id='nightAgentEntry';entry.innerHTML=`<section data-night-entry="0"><h3>Выберите тип агента</h3><label class="agent-channel-choice"><input type="radio" name="nightAgentRole" id="nightAgentEntryRole" value="primary" checked><span><b>Основной</b><small>Работает с клиентскими каналами после явного выбора подключения.</small></span></label><label class="agent-channel-choice"><input type="radio" name="nightAgentRole" value="expert"><span><b>Внутренний</b><small>Помогает основным агентам. Не получает внешние каналы и всегда создаётся в Manual.</small></span></label></section><section data-night-entry="1" hidden><h3>Как создать агента?</h3><label class="agent-channel-choice"><input type="radio" name="nightAgentTemplateChoice" value="technical-support" checked><span><b>Шаблон: Ночная техническая поддержка</b><small>Готовые инструкции, ночное расписание и передача сложных обращений человеку.</small></span></label><label class="agent-channel-choice"><input type="radio" name="nightAgentTemplateChoice" value="blank"><span><b>Создать с нуля</b><small>Заполните существующие настройки самостоятельно.</small></span></label></section>`;
  $('nightAgentNotice').insertAdjacentElement('afterend',entry);modal.querySelector('.night-agent-progress').id='nightAgentProgress';
  const localize=()=>window.tenantNightL10n?.translate(modal);new MutationObserver(localize).observe(modal,{subtree:true,childList:true,characterData:true});localize();
  opener?.addEventListener('click',()=>{if(!$('aiAgentsModal')?.hidden)$('aiAgentsModalClose')?.click();open()});
  modal.querySelectorAll('[data-night-close]').forEach(node=>node.addEventListener('click',close));
  document.addEventListener('keydown',event=>{if(modal.hidden)return;if(event.key==='Escape')close();if(event.key==='Tab'){const nodes=[...modal.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled])')].filter(node=>!node.closest('[hidden]')),first=nodes[0],last=nodes.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}}});
  $('nightAgentPrevious').addEventListener('click',()=>{if(entryStage<2){renderEntry(0);return}if(currentStep()===0){renderEntry(1);return}render(currentStep()-1,-1)});
  $('nightAgentNext').addEventListener('click',()=>{if(entryStage===0){agentRole=document.querySelector('[name=nightAgentRole]:checked')?.value==='expert'?'expert':'primary';dirty=true;renderEntry(1);return}if(entryStage===1){const chosen=document.querySelector('[name=nightAgentTemplateChoice]:checked')?.value==='blank'?'blank':'technical-support';if(chosen!==templateId){templateId=chosen;setupForm();}entryStage=2;render(0);setNotice('Мастер не меняет общие настройки компании.','good');return}try{validate(currentStep());dirty=true;saveProgress();setNotice('');render(currentStep()+1,1)}catch(error){showError(error.message)}});
  $('nightAgentCreate').addEventListener('click',create);
  $('nightAgentStart').addEventListener('change',scheduleHint);$('nightAgentEnd').addEventListener('change',scheduleHint);document.querySelectorAll('[name=nightAgentDay]').forEach(node=>node.addEventListener('change',scheduleHint));
  $('nightAgentScheduleEnabled').addEventListener('change',scheduleHint);$('nightAgentPipeline').addEventListener('change',renderKommoStages);
  $('nightAgentOpenEditor').onclick=()=>{const id=createdAgent?.id;if(!id)return;close();document.dispatchEvent(new CustomEvent('tenant-ai-edit-created-agent',{detail:{agentId:id}}))};
  $('nightAgentMode').addEventListener('change',modeHint);$('nightAgentLaunch').addEventListener('change',modeHint);
  document.querySelectorAll('#nightAgentWizard input,#nightAgentWizard select,#nightAgentWizard textarea').forEach(node=>node.addEventListener('input',()=>{if(entryStage===2&&!saving&&!completed){dirty=true;try{saveProgress()}catch(error){showError(error.message)}}}));
 }
 install();
 window.BEINTASKBOT_NIGHT_AGENT_TEMPLATE={nightAgentSchedule,nightAgentScope,nightAgentPayload};
 window.tenantAgentWizard=Object.freeze({open});
})();
