/* Manual review never invokes the send endpoint. Server permissions are authoritative. */
(() => {
  const $ = id => document.getElementById(id);
  let member, lead, epoch = 0;
  const identity = m => m?.tenant_id + ':' + m?.telegram_id;
  async function api(options, selected) {
    const response = await fetch('/api/platform/crm/chat/receipts' + (options ? '' : '?lead_id=' + encodeURIComponent(selected)),
      options ? {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(options)} : {credentials:'same-origin', cache:'no-store'});
    const result = await response.json();
    if (!response.ok || !result.success) throw new Error(result.error || 'Yoxlama alınmadı.');
    return result;
  }
  function button(label, action) {
    const b = document.createElement('button'); b.type='button'; b.className='outline'; b.textContent=label; b.onclick=action; return b;
  }
  function render() {
    $('chatSendReview')?.remove();
    if (!['owner','admin'].includes(member?.role) || !lead || !$('chatText')) return;
    const box=document.createElement('section'); box.id='chatSendReview'; box.className='panel'; box.style='margin-top:12px';
    const rows=document.createElement('div');
    const current=epoch, selected=lead, who=identity(member);
    const valid=()=>current===epoch && who===identity(member) && selected===lead && box.isConnected;
    const refresh=button('Göndərmə nəticələrini yoxla', async()=>{
      refresh.disabled=true; rows.textContent='Yüklənir…';
      try {
        const data=await api(null, selected); if(!valid())return;
        rows.replaceChildren();
        for(const receipt of data.receipts || []) {
          const row=document.createElement('div'); row.style='padding:10px 0;border-bottom:1px solid #ddd;overflow-wrap:anywhere';
          const info=document.createElement('p'); info.textContent=receipt.state+' · '+receipt.created_at+' · müəllif ID: '+receipt.actor_id+' · '+receipt.request_id;
          row.append(info);
          if(receipt.resolution?.manual){const note=document.createElement('p');note.textContent='Əl ilə yoxlanıb: '+receipt.resolution.reason;row.append(note);}
          if(receipt.state==='unknown') row.append(button('Nəticəni qeyd et',()=>review(receipt,row,valid)));
          rows.append(row);
        }
        if(!data.receipts?.length)rows.textContent='Göndərmə qeydi yoxdur.';
      } catch(error) {if(valid())rows.textContent=error.message;} finally {refresh.disabled=false;}
    });
    box.append(refresh,rows); $('chatText').parentElement.after(box);
  }
  function review(receipt,row,valid) {
    if(row.querySelector('form'))return;
    const form=document.createElement('form');
    const warning=document.createElement('p');warning.textContent='Əvvəl Kommo-da yoxlayın. Bu əl ilə qeyd edilən nəticədir, çatdırılma zəmanəti deyil. Mesaj təkrar göndərilmir. Göndərilmə qeyri-müəyyəndirsə, bağlayın.';
    const choice=document.createElement('select');
    for(const [value,text] of [['','Nəticəni seçin'],['found','Mesajı Kommo-da tapdım'],['not_sent','Göndərilmədiyini yoxladım']]){const option=document.createElement('option');option.value=value;option.textContent=text;choice.append(option);}
    choice.required=true;
    const reason=document.createElement('textarea');reason.placeholder='Necə yoxladınız? (məcburi)';reason.required=true;reason.minLength=5;reason.maxLength=1000;reason.style='width:100%;box-sizing:border-box';
    const save=button('Yoxlamanı təsdiqlə',null);save.type='submit';
    const close=button('Bağla',()=>form.remove());const notice=document.createElement('p');notice.setAttribute('role','status');
    const snapshot={lead_id:lead,actor_id:receipt.actor_id,request_id:receipt.request_id,review_id:crypto.randomUUID(),expected_updated_at:receipt.updated_at,expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id};
    let pending;
    form.onsubmit=async event=>{
      event.preventDefault();if(!valid()||save.disabled)return;
      if(!pending){if(!choice.value||reason.value.trim().length<5)return;pending={...snapshot,decision:choice.value,reason:reason.value.trim()};}
      save.disabled=true;close.disabled=true;choice.disabled=true;reason.disabled=true;notice.textContent='Yoxlama saxlanılır…';
      try {await api(pending);if(!valid())return;
        // Only the exact sender's command is removed, never another user's draft.
        try{const key='tenant-chat-send:'+identity(member)+':'+snapshot.lead_id;const stored=JSON.parse(sessionStorage.getItem(key)||'null');
          if(String(member.telegram_id)===String(snapshot.actor_id)&&stored?.request_id===snapshot.request_id){sessionStorage.removeItem(key);if(pending.decision==='found'&&$('chatText')?.value.trim()===stored.text)$('chatText').value='';if($('sendChat'))$('sendChat').textContent='Göndər';}
        }catch{/* Server result remains durable if browser storage is unavailable. */}
        notice.textContent='Nəticə qeyd edildi. Təkrar göndərmə edilmədi.';save.remove();choice.remove();reason.remove();
      } catch(error){if(valid())notice.textContent=error.message+' Eyni yoxlamanı yenidən təsdiqləyə bilərsiniz.';}
      finally {save.disabled=false;close.disabled=false;}
    };
    form.append(warning,choice,reason,save,close,notice);row.append(form);
  }
  document.addEventListener('tenant-profile',event=>{epoch++;member=event.detail.member;render();});
  document.addEventListener('tenant-chat-open',event=>{epoch++;lead=event.detail.lead_id;render();});
  const initial=epoch;
  fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if((initial===epoch||!member)&&data?.member){member=data.member;render();}}).catch(()=>{});
})();
