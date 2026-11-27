/* Tenant-scoped note creation. Unknown outcomes are checked, never resubmitted. */
(() => {
  const labels=()=>({ru:{add:'Добавить заметку',save:'Сохранить',cancel:'Отмена',check:'Проверить сохранение',unknown:'Результат пока не подтверждён. Проверьте сохранение, не создавая заметку повторно.',error:'Не удалось сохранить. Текст сохранён.',saved:'Заметка сохранена.'},en:{add:'Add note',save:'Save',cancel:'Cancel',check:'Check save',unknown:'Result unconfirmed. Check the saved request without creating another note.',error:'Could not save. Your text is preserved.',saved:'Note saved.'},az:{add:'Qeyd əlavə et',save:'Yadda saxla',cancel:'Ləğv et',check:'Saxlamanı yoxla',unknown:'Nəticə təsdiqlənməyib. Yenidən yaratmadan saxlamanı yoxlayın.',error:'Saxlama alınmadı. Mətn saxlanılıb.',saved:'Qeyd saxlanıldı.'}}[window.tenantI18n?.language]||{add:'Qeyd əlavə et',save:'Yadda saxla',cancel:'Ləğv et',check:'Saxlamanı yoxla',unknown:'Nəticə təsdiqlənməyib.',error:'Saxlama alınmadı.',saved:'Qeyd saxlanıldı.'});
  function mount({container,leadId,scope,current,canCreate,flow,onSaved}){
    const notes=container.querySelector('[data-related-notes]'),header=notes?.previousElementSibling;
    if(!header||!current()||!flow)return;
    const key=`deal-note:${scope.expected_tenant_id}:${scope.expected_user_id}:${leadId}`,c=labels();
    let pending=null,busy=false;try{pending=JSON.parse(sessionStorage.getItem(key)||'null');if(pending&&String(pending.lead_id)!==String(leadId))pending=null;}catch{}
    if(!canCreate&&!pending)return;
    const button=document.createElement('button');button.type='button';button.className='outline';button.textContent='+ '+c.add;button.dataset.relatedNoteCreate='';header.append(button);
    const dialog=document.createElement('dialog');dialog.className='deal-note-dialog';dialog.setAttribute('aria-label',c.add);
    const form=document.createElement('form');form.className='deal-note-form';
    const input=document.createElement('textarea');input.required=true;input.maxLength=10000;input.rows=4;input.setAttribute('aria-label',c.add);input.style.cssText='width:100%;box-sizing:border-box;color:var(--ink);background:var(--card)';
    const save=document.createElement('button');save.type='submit';save.className='primary';
    const cancel=document.createElement('button');cancel.type='button';cancel.className='outline';cancel.textContent=c.cancel;
    const notice=document.createElement('p');notice.setAttribute('role','status');
    const title=document.createElement('h3');title.textContent=c.add;
    form.append(title,input,save,cancel,notice);dialog.append(form);header.after(dialog);
    dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
    const style=document.createElement('style');style.textContent='.deal-note-dialog{box-sizing:border-box;width:min(520px,calc(100vw - 24px));max-height:calc(100dvh - 24px);overflow:auto;border:1px solid var(--line);border-radius:12px;padding:20px;color:var(--ink);background:var(--card)}.deal-note-dialog::backdrop{background:#0008}.deal-note-form textarea{margin:10px 0}.deal-note-form button{min-height:44px;margin-inline-end:8px}';dialog.append(style);
    function render(){input.disabled=busy||!!pending;save.disabled=busy||(!pending&&!canCreate);cancel.disabled=busy;save.textContent=pending?c.check:c.save;}
    button.onclick=()=>{if(!current())return;if(pending)input.value=pending.text;render();if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');input.focus();};
    cancel.onclick=()=>{if(!busy){if(typeof dialog.close==='function')dialog.close();else dialog.removeAttribute('open');button.focus();}};
    form.onsubmit=async event=>{event.preventDefault();if(busy||!current()||!pending&&!canCreate)return;
      const checking=!!pending;
      if(!pending){const text=input.value.trim();if(!text||text.length>10000)return;const command={request_id:crypto.randomUUID(),lead_id:String(leadId),text};try{sessionStorage.setItem(key,JSON.stringify(command));pending=command;}catch{notice.textContent=c.error;return;}}
      const command=pending;busy=true;render();notice.textContent='';
      try{const result=await flow.api('/api/platform/crm/deal/notes'+(checking?'?'+new URLSearchParams({request_id:command.request_id}):''),{scope,method:checking?'GET':'POST',...(checking?{}:{body:command})});
        if(!current())return;
        if(result.state!=='confirmed'||result.request_id!==command.request_id||String(result.lead_id)!==String(leadId)||!result.note?.id||result.note.text!==command.text)throw Object.assign(Error('unknown'),{unknown:true});
        sessionStorage.removeItem(key);pending=null;input.value='';notice.textContent=c.saved;
        if(onSaved)onSaved(result.note);
        else if(!notes.querySelector('[data-note-id="'+String(result.note.id).replace(/[^0-9]/g,'')+'"]')){const article=document.createElement('article');article.className='kommo-related-card kommo-note-card';article.dataset.noteId=String(result.note.id);article.textContent=result.note.text;if(!notes.querySelector('article'))notes.replaceChildren();notes.prepend(article);}
      }catch(error){if(!current())return;if(error.keepRequest===false||error.keepRequest!==true&&!checking&&!error.unknown){sessionStorage.removeItem(key);pending=null;}notice.textContent=pending?c.unknown:c.error;}
      finally{busy=false;if(current())render();}
    };render();
  }
  window.tenantKommoDealNoteCreate={mount};
})();
