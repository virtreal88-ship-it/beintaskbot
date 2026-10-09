/* Explicit button only. Task completion does not dispatch deal completion. */
(() => {
  let profile=null, generation=0;
  const identity=()=>profile?`${profile.tenant_id}:${profile.telegram_id}`:'';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(path,data){
    const response=await fetch('/api/platform/crm/'+path,{credentials:'same-origin',...(data?{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...data,expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id})}: {})});
    const result=await response.json();
    if(!response.ok||!result.success){const e=Error(result.error||'Sorğu alınmadı.');e.keep=result.keep_request||response.status>=500;throw e;}
    return result;
  }
  const panel=document.createElement('section');panel.className='panel';panel.hidden=true;panel.style.marginTop='20px';
  panel.innerHTML='<h2>Sövdələşmə təsdiqləri</h2><button type="button" class="outline refresh">Yenilə</button><p class="sub notice-text" role="status"></p><div class="deal-review-list"></div><button type="button" class="outline more" hidden>Daha çox</button>';
  document.getElementById('systemApprovalsHost').append(panel);
  let offset=0;
  const list=panel.querySelector('.deal-review-list'),notice=panel.querySelector('.notice-text'),more=panel.querySelector('.more'),refresh=panel.querySelector('.refresh');
  async function load(append=false){
    const revision=++generation,snapshot=identity();refresh.disabled=true;more.disabled=true;
    try{
      const result=await api('deal-approvals?limit=50&offset='+(append?offset:0));
      if(revision!==generation||snapshot!==identity()||panel.hidden)return;
      if(!append){list.replaceChildren();offset=0;}
      for(const item of result.approvals||[]){
        const card=document.createElement('article');card.className='member';card.style.cssText='display:block;border-bottom:1px solid var(--line);padding:16px 0';
        card.innerHTML=`<b>${esc(item.name||'Sövdələşmə #'+item.lead_id)}</b><p class="sub">${esc(item.creator_name)} · #${esc(item.lead_id)}</p><p style="white-space:pre-wrap">${esc(item.result_text)}</p>`;
        if(item.step==='waiting_approval'){
          for(const [action,label] of [['approve','Təsdiqlə'],['reject','Rədd et']]){
            const button=document.createElement('button');button.type='button';button.className='outline';button.textContent=label;button.style.marginRight='8px';
            button.onclick=async()=>{
              card.querySelectorAll('button').forEach(b=>b.disabled=true);button.textContent='Gözləyin…';
              try{await api('deal-approvals',{action,creator_id:item.creator_id,request_id:item.request_id});if(snapshot!==identity())return;notice.textContent='Sorğu işlənildi.';await load();document.getElementById('dealsRefresh').click();}
              catch(e){if(snapshot!==identity())return;notice.textContent=e.message;await load();}
            };card.append(button);
          }
        }else{const text=document.createElement('p');text.textContent='Kommo nəticəsini yoxlayın. Təkrar tamamlamayın. Sorğu: '+item.request_id;card.append(text);}
        list.append(card);
      }
      offset=result.next_offset??(offset+(result.approvals?.length||0));more.hidden=!result.has_more;
      if(!list.children.length)list.textContent='Təsdiq gözləyən sövdələşmə yoxdur.';
    }catch(e){if(revision===generation)notice.textContent=e.message;}
    finally{if(revision===generation){refresh.disabled=false;more.disabled=false;}}
  }
  refresh.onclick=()=>load();more.onclick=()=>load(true);
  document.addEventListener('tenant-chat-open',event=>{
    if(!profile)return;
    const host=document.getElementById('modalContent'),button=document.createElement('button'),snapshot=identity();
    const lead=Number(event.detail.lead_id);if(!Number.isSafeInteger(lead)||lead<=0)return;
    button.type='button';button.className='outline';button.textContent='Sövdələşməni tamamla';button.style.margin='12px 0';host.prepend(button);
    button.onclick=async()=>{
      const key=`crm-deal-complete:${snapshot}:${lead}`;let pending;
      try{pending=JSON.parse(sessionStorage.getItem(key)||'null');}
      catch{alert('Sorğu yaddaşı əlçatan deyil.');return;}
      if(!pending&&!confirm('Sövdələşməni uğurla tamamlamaq istəyirsiniz?'))return;
      pending||={lead_id:lead,request_id:crypto.randomUUID(),result_text:''};
      try{sessionStorage.setItem(key,JSON.stringify(pending));}catch{alert('Sorğu saxlanmadı.');return;}
      button.disabled=true;button.innerHTML='<i class="spinner" style="display:inline-block"></i>Gözləyin…';
      try{
        const result=await api('deals/complete',pending);sessionStorage.removeItem(key);
        if(snapshot!==identity())return;
        button.textContent=result.approval_pending?'Təsdiq gözləyir':'Tamamlandı';
        if(result.completed)document.getElementById('dealsRefresh').click();
        if(!panel.hidden)load();
      }catch(e){
        if(!e.keep)sessionStorage.removeItem(key);if(snapshot!==identity())return;
        alert(e.message);button.textContent=e.keep?'Eyni sorğunu yoxla':'Sövdələşməni tamamla';button.disabled=false;
      }
    };
  });
  function apply(data){
    generation++;profile=data.capabilities?.modules?.deals?data.member:null;
    panel.hidden=!profile||!['owner','admin'].includes(profile.role);list.replaceChildren();notice.textContent='';
    if(!panel.hidden)load();
  }
  let received=false;document.addEventListener('tenant-profile',e=>{received=true;apply(e.detail);});
  fetch('/api/platform/me',{credentials:'same-origin'}).then(r=>r.json()).then(d=>{if(d.success&&!received)apply(d);}).catch(()=>{});
})();
