/* One owner-only alert, revalidated by the server before showing source context. */
(()=>{
  'use strict';
  const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const copy={
    ru:{title:'Сигнал по задаче',close:'Закрыть',loading:'Проверяем источник…',gone:'Этот сигнал уже недоступен или больше не подтверждается источником.',error:'Не удалось проверить источник. Откройте уведомление позже.',what:'Что произошло',quote:'По тексту задачи (сообщение автора, не проверенный инцидент)',missingText:'Описание не заполнено. Подробности нужно уточнить в исходной задаче.',due:'Плановая дата в источнике',updated:'Запись обновлена в источнике',project:'Проект',status:'Статус',priority:'Приоритет задачи',priorities:['Не указан','Срочный','Высокий','Обычный','Низкий'],assigned:'В задаче указан ответственный.',unassigned:'В задаче не назначен ответственный. Это не означает, что никто не работает над ней.',why:'Почему стоит обратить внимание',next:'Что проверить дальше',unknown:'Что пока неизвестно',limits:'Работа системы, фактический ущерб и наличие временного решения этой проверкой не подтверждены. Комментарии и действия команды за пределами задачи не проверялись.',steps:'Проверьте, кто уже занимается задачей, и уточните текущий статус и временное решение. Если ответственного нет, согласуйте его назначение.',price_access:'В тексте есть упоминания цен или оплаты и прав доступа. Это повод проверить правила цен, оплаты и доступа; сам текст ещё не доказывает нарушение.',access:'В тексте есть вопрос о правах доступа. Стоит проверить, какие действия доступны затронутой роли.',price:'В тексте есть вопрос об изменении цены или оплаты. Стоит проверить правила цен и реальные операции.',outage:'В тексте есть сообщение о сбое. Стоит проверить, сохраняется ли проблема и кому она мешает.',unassignedWhy:'Срочная или важная задача остаётся без назначенного ответственного: нужно уточнить, кто её ведёт.',overdueWhy:'Плановая дата задачи прошла. Нужно уточнить фактический статус; это само по себе не доказывает ущерб.',staleWhy:'Запись давно не обновлялась. Это не означает, что работа остановилась.',genericWhy:'По задаче есть сигнал для проверки. Откройте источник и уточните текущую ситуацию.',checked:'Проверено',live:'Задача повторно прочитана из Linear при открытии.',cache:'Проверена недавняя сохранённая копия задачи CRM.',synced:'Копия обновлена',linear:'Открыть исходную задачу Linear',crm:'Открыть задачи CRM',original:'Текст источника показан на языке автора.',truncated:'Показан фрагмент. Полный текст доступен в исходной задаче.'},
    az:{title:'Tapşırıq üzrə siqnal',close:'Bağla',loading:'Mənbə yoxlanılır…',gone:'Bu siqnal artıq əlçatan deyil və ya mənbə ilə təsdiqlənmir.',error:'Mənbəni yoxlamaq mümkün olmadı. Bildirişi sonra açın.',what:'Nə baş verib',quote:'Tapşırığın mətninə görə (müəllifin məlumatıdır, təsdiqlənmiş insident deyil)',missingText:'Təsvir doldurulmayıb. Təfərrüatları mənbə tapşırığında dəqiqləşdirin.',due:'Mənbədə planlaşdırılan tarix',updated:'Mənbə qeydi yenilənib',project:'Layihə',status:'Status',priority:'Tapşırığın prioriteti',priorities:['Göstərilməyib','Təcili','Yüksək','Normal','Aşağı'],assigned:'Tapşırıqda məsul şəxs göstərilib.',unassigned:'Tapşırığa məsul şəxs təyin edilməyib. Bu, heç kimin onun üzərində işləmədiyini göstərmir.',why:'Niyə diqqət yetirmək lazımdır',next:'Növbəti nəyi yoxlamalı',unknown:'Hələ nə məlum deyil',limits:'Bu yoxlama sistemin işini, faktiki zərəri və müvəqqəti həllin mövcudluğunu təsdiqləmir. Şərhlər və komandanın tapşırıqdan kənar fəaliyyəti yoxlanılmayıb.',steps:'Tapşırıqla kimin məşğul olduğunu, cari vəziyyəti və müvəqqəti həlli dəqiqləşdirin. Məsul şəxs yoxdursa, təyinatı razılaşdırın.',price_access:'Mətndə qiymət və ya ödəniş, həmçinin giriş icazələri qeyd olunur. Qiymət, ödəniş və giriş qaydalarını yoxlamağa dəyər; mətn özü pozuntunu sübut etmir.',access:'Mətndə giriş icazələri ilə bağlı məsələ var. Müvafiq rolun hansı əməliyyatları edə bildiyini yoxlayın.',price:'Mətndə qiymət və ya ödəniş dəyişikliyi ilə bağlı məsələ var. Qiymət qaydalarını və faktiki əməliyyatları yoxlayın.',outage:'Mətndə nasazlıq barədə məlumat var. Problemin davam edib-etmədiyini və kimə mane olduğunu yoxlayın.',unassignedWhy:'Təcili və ya vacib tapşırığa məsul şəxs təyin edilməyib: kimin onu apardığını dəqiqləşdirin.',overdueWhy:'Tapşırığın planlaşdırılan tarixi keçib. Faktiki vəziyyəti dəqiqləşdirin; bu, özlüyündə zərər sübutu deyil.',staleWhy:'Qeyd uzun müddətdir yenilənməyib. Bu, işin dayandığını göstərmir.',genericWhy:'Tapşırıq üzrə yoxlanmalı siqnal var. Mənbəni açıb cari vəziyyəti dəqiqləşdirin.',checked:'Yoxlanılıb',live:'Tapşırıq açılarkən Linear-dan yenidən oxunub.',cache:'CRM tapşırığının yaxın vaxtda saxlanmış nüsxəsi yoxlanılıb.',synced:'Nüsxə yenilənib',linear:'Linear-da mənbə tapşırığını aç',crm:'CRM tapşırıqlarını aç',original:'Mənbə mətni müəllifin dilində göstərilir.',truncated:'Mətnin bir hissəsi göstərilir. Tam mətn mənbə tapşırığında mövcuddur.'},
    en:{title:'Task signal',close:'Close',loading:'Checking the source…',gone:'This signal is no longer available or supported by its source.',error:'Could not check the source. Open this notification again later.',what:'What happened',quote:'From the task text (a report by its author, not a verified incident)',missingText:'No description is available. Check the original task for details.',due:'Target date in source',updated:'Source record updated',project:'Project',status:'Status',priority:'Task priority',priorities:['Not specified','Urgent','High','Normal','Low'],assigned:'The task has an assignee.',unassigned:'The task has no assignee. This does not mean nobody is working on it.',why:'Why it needs attention',next:'What to check next',unknown:'What is still unknown',limits:'This check does not confirm system behavior, actual damage, or a temporary workaround. Comments and team activity outside the task were not checked.',steps:'Check who is already handling the task, its current status, and any workaround. If there is no assignee, agree who should take ownership.',price_access:'The text mentions prices or payments and access permissions. Pricing, payment and access rules may need checking; the text alone does not prove a violation.',access:'The text raises an access-permission concern. Check which actions the affected role can perform.',price:'The text raises a price or payment concern. Check pricing rules and actual transactions.',outage:'The text reports a service problem. Check whether it is still occurring and who is affected.',unassignedWhy:'An urgent or high-priority task has no assignee: clarify who is handling it.',overdueWhy:'The task target date has passed. Check its actual status; this alone does not prove damage.',staleWhy:'The record has not been updated recently. This does not mean work has stopped.',genericWhy:'There is a signal to review on this task. Open the source and check the current situation.',checked:'Checked',live:'The task was read again from Linear when opened.',cache:'A recently saved copy of the CRM task was checked.',synced:'Copy updated',linear:'Open the original Linear task',crm:'Open CRM tasks',original:'Source text is shown in its original language.',truncated:'An excerpt is shown. Open the original task for the full text.'}
  };
  const language=()=>{const l=window.tenantI18n?.language||document.documentElement.lang||'az';return copy[l]?l:'az';};
  const t=key=>copy[language()][key];
  let profile=window.tenantProfileSnapshot||null,identity='',epoch=0,overlay=null,display=null;
  const memberOf=data=>data?.member||null;
  const scopeOf=data=>{const m=memberOf(data);return m?`${m.tenant_id||''}:${m.telegram_id||''}:${m.role||''}:${m.active===true}`:'';};
  function close(removeParam=false){epoch++;overlay?.remove();overlay=null;display=null;if(removeParam){const url=new URL(location.href);url.searchParams.delete('risk_alert_id');history.replaceState(history.state,'',url.pathname+url.search+url.hash);}}
  function addText(parent,tag,value){const node=document.createElement(tag);node.textContent=String(value??'');parent.append(node);return node;}
  function stamp(value){const date=new Date(value);return value&&!Number.isNaN(date.getTime())?new Intl.DateTimeFormat({ru:'ru-RU',az:'az-AZ',en:'en-GB'}[language()],{dateStyle:'medium',timeStyle:'short'}).format(date):'';}
  function showAlert(alert){
    display=alert;overlay?.remove();const shade=document.createElement('div');shade.className='tenant-risk-alert-shade';shade.setAttribute('role','presentation');shade.style.cssText='position:fixed;inset:0;z-index:10050;background:#0008;display:grid;place-items:center;padding:16px';
    const dialog=document.createElement('section');dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-labelledby','riskAlertTitle');dialog.style.cssText='box-sizing:border-box;width:min(620px,100%);max-height:85vh;overflow:auto;overflow-wrap:anywhere;background:var(--chakra-colors-white,#fff);color:var(--chakra-colors-gray-800,#222);border-radius:14px;padding:22px;box-shadow:0 16px 60px #0004;line-height:1.5';
    const head=document.createElement('header');head.style.cssText='display:flex;align-items:start;justify-content:space-between;gap:12px';
    const c=alert.context||{},title=addText(head,'h2',c.title||alert.source_title||t('title'));title.id='riskAlertTitle';title.style.margin='0';
    const closeButton=addText(head,'button',t('close'));closeButton.type='button';closeButton.onclick=()=>close(true);dialog.append(head);
    if(alert.messageKey){addText(dialog,'p',t(alert.messageKey));}
    else {
      addText(dialog,'p',[alert.source_kind==='linear'?'Linear':'CRM',c.identifier,c.project?`${t('project')}: ${c.project}`:''].filter(Boolean).join(' · '));
      addText(dialog,'h3',t('what'));
      addText(dialog,'p',t('quote'));
      if(c.title)addText(dialog,'p',c.title);
      addText(dialog,'p',c.excerpt||t('missingText'));
      addText(dialog,'small',t('original')+(c.excerpt_truncated?' '+t('truncated'):''));
      if(c.priority)addText(dialog,'p',`${t('priority')}: ${t('priorities')[c.priority]||t('priorities')[0]}`);
      if(c.due_date)addText(dialog,'p',`${t('due')}: ${c.due_date}`);
      const updated=stamp(c.source_updated_at);if(c.stale_record&&updated)addText(dialog,'p',`${t('updated')}: ${updated}`);
      if(c.status)addText(dialog,'p',`${t('status')}: ${c.status}`);
      if(typeof c.assigned==='boolean')addText(dialog,'p',t(c.assigned?'assigned':'unassigned'));
      addText(dialog,'h3',t('why'));
      const reasons=[];
      if(['price_access','access','price','outage'].includes(c.topic))reasons.push(c.topic);
      if(alert.rule_id==='high_priority_unassigned'||(c.priority<=2&&c.priority>=1&&c.assigned===false))reasons.push('unassignedWhy');
      if(c.overdue)reasons.push('overdueWhy');
      if(c.stale_record)reasons.push('staleWhy');
      (reasons.length?reasons:['genericWhy']).forEach(key=>addText(dialog,'p',t(key)));
      addText(dialog,'h3',t('unknown'));addText(dialog,'p',t('limits'));
      addText(dialog,'h3',t('next'));addText(dialog,'p',t('steps'));
      const checked=stamp(alert.checked_at);if(checked)addText(dialog,'small',`${t('checked')}: ${checked}`);
      if(c.source_mode){addText(dialog,'p',t(c.source_mode==='live'?'live':'cache'));const synced=stamp(c.source_checked_at);if(synced)addText(dialog,'small',`${t('synced')}: ${synced}`);}
      if(alert.source_kind==='crm_task'&&alert.source_url==='/app?view=tasks'){
        const a=addText(dialog,'a',t('crm'));a.href='/app?view=tasks';a.style.display='block';
      }else if(alert.source_kind==='linear'&&typeof alert.source_url==='string'&&alert.source_url.startsWith('https://linear.app/')){
        const a=addText(dialog,'a',t('linear'));a.href=alert.source_url;a.target='_blank';a.rel='noopener noreferrer';a.style.display='block';
      }
    }
    shade.append(dialog);shade.addEventListener('click',event=>{if(event.target===shade)close(true);});shade.addEventListener('keydown',event=>{
      if(event.key==='Escape'){event.preventDefault();close(true);}
      if(event.key==='Tab'){const controls=[...dialog.querySelectorAll('button,a[href]')],first=controls[0],last=controls.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
    });document.body.append(shade);overlay=shade;closeButton.focus();
  }
  function showMessage(messageKey){showAlert({messageKey});}
  async function route(){
    const id=new URL(location.href).searchParams.get('risk_alert_id');if(!id){if(overlay)close(false);return;}
    const token=++epoch,owner=memberOf(profile),scope=scopeOf(profile);if(!uuid.test(id)||owner?.role!=='owner'||owner.active!==true){close(false);return;}
    showMessage('loading');
    try{const response=await fetch(`/api/platform/risks/alerts/${encodeURIComponent(id)}`,{credentials:'same-origin',headers:{'Accept':'application/json','X-Tenant-Id':String(owner.tenant_id||''),'X-User-Id':String(owner.telegram_id||'')}});const data=await response.json();if(token!==epoch||scope!==scopeOf(profile)||!overlay?.isConnected)return;if(response.ok||response.status===404)document.dispatchEvent(new CustomEvent('tenant-risk-alert-read',{detail:{alert_id:id}}));if(!response.ok||!data.success||!data.alert){showMessage(response.status===404?'gone':'error');return;}showAlert(data.alert);
    }catch{if(token===epoch&&scope===scopeOf(profile)&&overlay?.isConnected)showMessage('error');}
  }
  function apply(data){const next=scopeOf(data);if(identity&&identity!==next)close(true);profile=data;identity=next;if(memberOf(data)?.role!=='owner'||memberOf(data)?.active!==true)close(false);else void route();}
  document.addEventListener('tenant-profile',event=>apply(event.detail));
  document.addEventListener('tenant-language',()=>{if(display&&overlay?.isConnected)showAlert(display);});
  document.addEventListener('tenant-view',event=>{if(event.detail?.view!=='linear'&&overlay)close(true);});
  for(const type of ['tenant-auth-invalid','tenant-auth-pending'])document.addEventListener(type,()=>{profile=null;identity='';close(false);});
  window.addEventListener('popstate',()=>void route());
  if(profile)apply(profile);else if(new URL(location.href).searchParams.has('risk_alert_id'))document.addEventListener('tenant-profile',event=>apply(event.detail),{once:true});
})();
