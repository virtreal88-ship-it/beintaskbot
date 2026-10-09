/* Read-only phone search. Results are candidates; this UI never merges records. */
(() => {
  let member;
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  document.head.insertAdjacentHTML('beforeend',`<style>
    .phone-duplicates-overlay{position:fixed;inset:0;z-index:30000;background:#0f172a77;display:grid;place-items:center;padding:16px}
    .phone-duplicates-dialog{background:white;border-radius:18px;padding:20px;width:min(530px,100%);max-height:calc(100dvh - 32px);overflow:auto;color:#16372f}
    .phone-duplicates-dialog header{display:flex;justify-content:space-between;align-items:center;gap:12px}.phone-duplicates-dialog button{border-radius:9px;padding:9px 12px;border:1px solid #d6e3dd;background:#f5faf7;color:#15594a;cursor:pointer}
    .phone-duplicates-dialog form{display:flex;gap:8px;margin:14px 0}.phone-duplicates-dialog input{min-width:0;flex:1;border:1px solid #d6e3dd;border-radius:9px;padding:10px;font-size:16px}
    .phone-duplicates-result{border:1px solid #e0e9e4;border-radius:12px;margin-top:10px;padding:12px}.phone-duplicates-result small{display:block;margin:5px 0;color:#61776d}.phone-duplicates-result b{display:block;overflow-wrap:anywhere}.phone-duplicates-dialog [role=status]{font-size:13px;overflow-wrap:anywhere}
    .phone-duplicates-dialog button:disabled{opacity:.6;cursor:wait}.phone-duplicates-spinner{display:inline-block;width:12px;height:12px;border:2px solid #c9ded4;border-top-color:#167557;border-radius:50%;animation:duplicate-spin 1s linear infinite;margin-right:6px}@keyframes duplicate-spin{to{transform:rotate(360deg)}}
    @media(prefers-reduced-motion:reduce){.phone-duplicates-spinner{animation:none}}
  </style>`);
  let closeActive=()=>{};
  document.addEventListener('tenant-profile',event=>{closeActive();member=event.detail.member;});
  document.addEventListener('click',event=>{
    const trigger=event.target.closest?.('[data-duplicate-search]');
    if(!trigger)return;
    const tenant=trigger.dataset.duplicateTenant==='true',lead=Number(trigger.dataset.duplicateSearch);
    if(!lead)return;
    closeActive();
    const identity=tenant?member?.tenant_id+':'+member?.telegram_id:String(typeof CURRENT_USER_ID!=='undefined'?CURRENT_USER_ID:'');
    if(tenant&&!member)return;
    const modal=document.createElement('div');modal.className='phone-duplicates-overlay';
    modal.innerHTML=`<section class="phone-duplicates-dialog" role="dialog" aria-modal="true" aria-label="Dublikatları tap">
      <header><b>Dublikatları tap</b><button type="button" data-close aria-label="Bağla">✕</button></header>
      <p style="font-size:13px;color:#61776d">Yalnız telefon nömrəsinə görə axtarılır. +994, 0 və yerli format eyni sayılır. Sövdələşmələr dəyişdirilmir.</p>
      <form><input type="tel" inputmode="tel" maxlength="80" required aria-label="Telefon nömrəsi" placeholder="+99455…"><button type="submit">Axtar</button></form>
      <div role="status"></div><div data-results></div></section>`;
    document.body.append(modal);
    const input=modal.querySelector('input'),button=modal.querySelector('[type=submit]'),status=modal.querySelector('[role=status]'),results=modal.querySelector('[data-results]');
    input.value=trigger.dataset.duplicatePhone||'';
    let controller,busy=false;
    const valid=()=>modal.isConnected&&identity===(tenant?member?.tenant_id+':'+member?.telegram_id:String(typeof CURRENT_USER_ID!=='undefined'?CURRENT_USER_ID:''));
    const close=()=>{controller?.abort();modal.remove();if(closeActive===close)closeActive=()=>{};};closeActive=close;
    modal.querySelector('[data-close]').onclick=close;
    modal.onkeydown=event=>{if(event.key==='Escape')close();};
    modal.querySelector('form').onsubmit=async event=>{
      event.preventDefault();if(busy||!valid())return;
      busy=true;button.disabled=true;button.setAttribute('aria-busy','true');results.textContent='';
      status.innerHTML='<span class="phone-duplicates-spinner" aria-hidden="true"></span>Axtarılır…';
      controller=new AbortController();const timer=setTimeout(()=>controller.abort(),60000);
      try{
        const query=new URLSearchParams({lead_id:lead,phone:input.value});
        const headers={};if(!tenant)headers['X-TG-User-ID']=identity;
        else{query.set('expected_tenant_id',member.tenant_id);query.set('expected_user_id',member.telegram_id);}
        const response=await fetch((tenant?'/api/platform/crm/deals/duplicates?':'/api/deal/duplicates?')+query,
          {headers,credentials:'same-origin',cache:'no-store',signal:controller.signal});
        const data=await response.json();if(!valid())return;
        if(!response.ok||!data.success)throw Error(data.error||'Axtarış alınmadı.');
        const deals=data.deals||[];
        status.textContent=deals.length?`${data.phone} · ${deals.length} uyğun sövdələşmə. Eyni nömrə fərqli sifarişlərə aid ola bilər.`:'Bu nömrə ilə sizə açıq başqa sövdələşmə tapılmadı.';
        if(data.incomplete)status.textContent+=' Axtarış limitinə çatıldı; siyahı tam olmaya bilər.';
        results.innerHTML=deals.map(row=>`<div class="phone-duplicates-result"><b>${esc(row.contact_name||row.name)}</b><small>${esc(row.phone)} · #${esc(row.id)}</small><div>${esc(row.name)}</div><small>${esc(row.pipeline_name)} · ${esc(row.stage_name)}</small><button type="button" data-open="${Number(row.id)}">Baxış</button></div>`).join('');
        results.querySelectorAll('[data-open]').forEach(open=>open.onclick=()=>{
          if(!valid())return;const id=Number(open.dataset.open);close();
          if(tenant)document.dispatchEvent(new CustomEvent('tenant-duplicate-open',{detail:{lead_id:id}}));
          else if(typeof openDealViewById==='function')openDealViewById(id);
        });
      }catch(error){if(valid())status.textContent=controller.signal.aborted?'Axtarışın vaxtı bitdi. Yenidən yoxlayın.':error.message;}
      finally{clearTimeout(timer);busy=false;button.disabled=false;button.setAttribute('aria-busy','false');}
    };
    input.focus();
  });
  const initial=member;
  if(location.pathname==='/app')fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(member===initial&&data?.member)member=data.member;}).catch(()=>{});
})();
