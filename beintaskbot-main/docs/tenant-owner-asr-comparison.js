/* One approved owner experiment, mounted in the existing dialogue diagnostics. */
(()=>{
 const approved='691129c0239935f2dc4d0c19740371c36615845e9b94aca4e862e776c6984ec2';
 const endpoint=(window.TENANT_AI_AGENTS_CONFIG?.endpoint||'/api/platform/ai/')+'dialogs/asr-comparison';
 const $=id=>document.getElementById(id),node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
 const copy={ru:{historical:'Сохранённый результат прежней версии источника или настроек. Только чтение; повтор запрещён.',title:'Сравнение двух ASR для этого аудио',preview:'Подготовить сравнение',refresh:'Обновить статус',notice:'Один запуск: whisper-1 и gpt-4o-mini-transcribe, язык az, без подсказки и без генерации ответа. Внутренний предел резервирования — $0.06 за два вызова; это не счёт OpenAI. Рабочая транскрипция не изменится.',confirm:'Подтверждаю ровно два вызова с общим пределом $0.06',run:'Выполнить два ASR-вызова',blocked:'Запуск заблокирован настройками бюджета или тарифов. Настройки здесь не меняются.',locked:'Запуск уже допущен. Повтор отключён, в том числе при неизвестном результате.',usage:'Usage провайдера (если получен)',internal:'Внутренний резерв / accounting state'},az:{historical:'Mənbənin və ya ayarların əvvəlki versiyasının saxlanmış nəticəsi. Yalnız oxuma; təkrar qadağandır.',title:'Bu səs üçün iki ASR müqayisəsi',preview:'Müqayisəni hazırla',refresh:'Statusu yenilə',notice:'Bir icra: whisper-1 və gpt-4o-mini-transcribe, az dili, ipucusuz və cavab yaratmadan. İki sorğu üçün daxili rezerv həddi $0.06-dır; bu, OpenAI hesab-fakturası deyil. İşçi transkripsiya dəyişməyəcək.',confirm:'Cəmi $0.06 həddi ilə dəqiq iki sorğunu təsdiqləyirəm',run:'İki ASR sorğusunu icra et',blocked:'Büdcə və ya tarif ayarları icranı bloklayır. Burada ayarlar dəyişmir.',locked:'İcra artıq qəbul edilib. Naməlum nəticə daxil olmaqla təkrar söndürülüb.',usage:'Provayder usage (əldə edilibsə)',internal:'Daxili rezerv / accounting state'},en:{historical:'Saved result from an earlier source or settings version. Read only; rerun is disabled.',title:'Two-ASR comparison for this audio',preview:'Prepare comparison',refresh:'Refresh status',notice:'One execution: whisper-1 and gpt-4o-mini-transcribe, az language, no prompt and no answer generation. Internal reserve cap is $0.06 for two calls; this is not an OpenAI invoice. The working transcript stays unchanged.',confirm:'Confirm exactly two calls within a total $0.06 cap',run:'Execute two ASR calls',blocked:'Budget or tariff configuration blocks execution. Settings are not changed here.',locked:'Execution already admitted. Retry is disabled, including unknown outcomes.',usage:'Provider usage (when received)',internal:'Internal reserve / accounting state'}};
 const t=key=>(copy[window.tenantI18n?.language]||copy.az)[key];
 const member=()=>window.tenantOperator?.member;
 const identity=()=>`${member()?.tenant_id}:${member()?.telegram_id}`;
 const routeKey=r=>JSON.stringify([r?.channel_id,r?.conversation_id,r?.lead_id]);
 let scanning=false;
 async function hash(value){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(value)));return [...new Uint8Array(bytes)].map(x=>x.toString(16).padStart(2,'0')).join('');}
 async function mount(){
  if(scanning||$('ownerASRComparison'))return;
  const modal=$('operatorDiagnosticsModal'),body=$('operatorDiagnosticsBody'),route=window.tenantOperator?.currentDialog,m=member();
  if(!crypto.subtle||!body||modal?.hidden||m?.role!=='owner'||route?.provider!=='connect'||!route.agent_id)return;
  scanning=true;
  try{
   const owner=identity(),key=routeKey(route);let source;
   for(const audio of document.querySelectorAll('#chatRows audio.operator-native-audio')){
    const host=audio.closest('[data-chat-attachment]'),message=host?.dataset.chatAttachment||audio.dataset.messageId||new URL(audio.src,location.origin).searchParams.get('message_id');
    if(message&&await hash([String(m.tenant_id),String(route.channel_id),String(route.conversation_id),message])===approved){source={channel_id:route.channel_id,conversation_id:route.conversation_id,message_id:message};break;}
   }
   if(!source||owner!==identity()||key!==routeKey(window.tenantOperator?.currentDialog)||!body.isConnected||modal.hidden||$('ownerASRComparison'))return;
   const card=node('section');card.id='ownerASRComparison';body.append(card);
   const current=()=>owner===identity()&&member()?.role==='owner'&&key===routeKey(window.tenantOperator?.currentDialog)&&card.isConnected&&!modal.hidden;
   const button=(text,fn)=>{const b=node('button',text);b.type='button';b.onclick=fn;return b;};
   async function api(payload){if(!current())throw Error('Identity or dialogue changed');const me=member(),response=await fetch(endpoint+(payload?'':'?'+new URLSearchParams(source)),{method:payload?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-Tenant-Id':String(me.tenant_id),'X-User-Id':String(me.telegram_id)},...(payload?{body:JSON.stringify({...source,...payload,expected_tenant_id:me.tenant_id,expected_user_id:me.telegram_id})}:{})});const result=await response.json();if(!current())throw Error('Identity or dialogue changed');if(!response.ok||result.success===false)throw Error(result.error||'Comparison unavailable');return result.result;}
   function draw(result){
    if(!current())return;card.replaceChildren(node('h3',t('title')),node('p',t('notice')));
    const status=node('p');status.setAttribute('role','status');card.append(status);
    const refresh=button(t('refresh'),async()=>{refresh.disabled=true;try{draw(await api());}catch(error){if(current())status.textContent=error.message;}finally{if(refresh.isConnected)refresh.disabled=false;}});card.append(refresh);
    if(!result){refresh.textContent=t('preview');return;}
    if(result.blocked){status.textContent=t('blocked')+' '+String(result.code||'');return;}
    if(result.byte_hash)card.append(node('pre',JSON.stringify({original_sha256:result.byte_hash,upload_sha256:result.upload_hash,upload_mime:result.upload_mime,upload_extension:result.upload_extension,duration_seconds:result.duration_seconds},null,2)));
    if(result.admitted)card.append(node('p',t('locked')));if(result.historical)card.append(node('p',t('historical')));
    for(const variant of result.variants||[]){
     const part=node('article');part.append(node('strong',`${variant.model} · ${variant.state||'az'}`),node('p',`${t('internal')}: $${(Number(variant.reserve_microusd)/1e6).toFixed(5)} · ${variant.accounting_state||'preview'}`));
     if(variant.parameters)part.append(node('pre',JSON.stringify(variant.parameters,null,2)));
     if(variant.result){part.append(node('p',variant.result.transcript||''),node('small',t('usage')),node('pre',JSON.stringify(variant.result.usage||{},null,2)),node('pre',JSON.stringify(variant.result.diagnostics||{},null,2)));}
     if(variant.code)part.append(node('small',variant.code));card.append(part);
    }
    if(result.can_execute){
     const label=node('label',t('confirm')),confirmed=node('input');confirmed.type='checkbox';confirmed.dataset.ownerAsrConfirm='';label.prepend(confirmed);
     const run=button(t('run'),async()=>{if(!confirmed.checked||!current())return;run.disabled=true;confirmed.disabled=true;refresh.disabled=true;try{draw(await api({version:result.version,confirmed:true,max_calls:2,max_cost_microusd:60000}));}catch(error){if(current()){status.textContent=error.message;run.remove();confirmed.disabled=true;refresh.disabled=false;}}});run.dataset.ownerAsrExecute='';run.disabled=true;confirmed.onchange=()=>{run.disabled=!confirmed.checked;};card.append(label,run);
    }
   }
   draw();
  }finally{scanning=false;}
 }
 new MutationObserver(()=>void mount()).observe(document.body,{childList:true,subtree:true});
 document.addEventListener('tenant-profile',()=>{$('ownerASRComparison')?.remove();void mount();});
 document.addEventListener('tenant-chat-close',()=>{$('ownerASRComparison')?.remove();});
 void mount();
})();
