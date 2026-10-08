/* Tenant ledger UI. Explicit administrator commands, never real bank transfers. */
(() => {
  const $=id=>document.getElementById(id),nav=document.querySelector('[data-view="finance"]');
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let profile,capabilities={},scope='',epoch=0,loading=false,busy=false,offset=0,total=0,rows=[],members=[],member=0,active=true,pending=null,action='credit',source=null;
  const modal=document.createElement('div');modal.id='financeModal';modal.className='modal';
  modal.innerHTML='<form class="modal-card" id="financeForm"><div class="modal-head"><h2 id="financeTitle"></h2><button class="close" type="button" id="financeClose">×</button></div><p id="financeTarget"></p><label id="financeAmountLabel">Məbləğ (AZN)<input id="financeAmount" inputmode="decimal" placeholder="100.00" maxlength="32"></label><label>Səbəb<textarea id="financeNote" maxlength="2000" required></textarea></label><p class="sub">Yalnız proqramdakı balans dəyişir. Bank köçürməsi edilmir.</p><div id="financeFormNotice" class="notice" role="status"></div><div class="finance-footer"><button type="submit" class="primary" id="financeSubmit">Təsdiq et</button><button type="button" class="outline" id="financeCancel">Bağla</button></div></form>';
  document.body.append(modal);
  const style=document.createElement('style');style.textContent='#view-finance .top{flex-wrap:wrap}#financeMember{max-width:240px;padding:9px;border:1px solid var(--line);border-radius:9px}#financeModal{z-index:50}#financeModal .modal-card{max-height:calc(100dvh - 24px);overflow:auto;padding-bottom:max(24px,env(safe-area-inset-bottom))}#financeModal label{display:grid;gap:8px;margin:14px 0}#financeModal input,#financeModal textarea{font:inherit;width:100%;padding:12px;border:1px solid var(--line);border-radius:10px}#financeModal textarea{resize:none;min-height:90px}#financeModal .finance-footer{position:sticky;bottom:-24px;padding:16px 0;background:white;display:flex;gap:10px}#financeModal .spinner{display:inline-block}';document.head.append(style);
  const key=()=>`crm-finance:${profile.tenant_id}:${profile.telegram_id}`;
  const privileged=()=>['owner','admin'].includes(profile?.role)&&!!capabilities.modules?.finance;
  const notice=(id,text)=>{$(id).textContent=text||'';$(id).classList.toggle('show',!!text);};
  async function api(url,data){const response=await fetch(url,{credentials:'same-origin',cache:'no-store',...(data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});const body=await response.json();if(!response.ok||!body.success){const error=Error(body.error||'Sorğu alınmadı.');error.status=response.status;throw error;}return body;}
  function buttons(){
    for(const id of ['financeCredit','financeDebit'])$(id).hidden=!privileged()||!active;
    $('financePrev').disabled=loading||busy||offset===0;$('financeNext').disabled=loading||busy||offset+50>=total;
    $('financeMember').disabled=loading||busy;$('financeRefresh').disabled=loading||busy;
  }
  function render(){
    const labels={credit:'Mədaxil',debit:'Məxaric',reverse:'Əks əməliyyat'};
    $('financeEntries').innerHTML=rows.map(row=>`<article class="hot-card"><header><h2>${esc(row.amount)} AZN</h2><span class="badge">${esc(labels[row.kind])}</span></header><p class="hot-text">${esc(row.note)}</p><div class="sub">${esc(row.created_at?.replace('T',' ').slice(0,19))} UTC · ${esc(row.actor_name||'Administrator')}</div>${row.reverses_id?`<div class="sub">Əvvəlki əməliyyat: ${esc(row.reverses_id)}</div>`:''}${privileged()&&row.kind!=='reverse'?`<button class="outline" data-finance-reverse="${esc(row.id)}" style="margin-top:10px">Geri al</button>`:''}</article>`).join('')||'<div class="empty">Əməliyyat yoxdur.</div>';
    $('financePage').textContent=total?`${offset+1}–${Math.min(offset+rows.length,total)} / ${total}`:'0';buttons();
  }
  async function load(target=0){
    if(!capabilities.modules?.finance||loading||busy)return;const version=epoch;loading=true;buttons();notice('financeNotice','');
    try{const body=await api(`/api/platform/finance?member_id=${encodeURIComponent(member)}&limit=50&offset=${target}`);if(version!==epoch)return;
      rows=body.entries||[];total=body.total||0;offset=target;active=body.active;$('financeBalance').textContent=`${body.display_name}: ${body.balance} ${body.currency}`;render();
    }catch(error){if(version===epoch)notice('financeNotice',error.message);}
    finally{if(version===epoch){loading=false;buttons();}}
  }
  function lock(on){$('financeAmount').disabled=on;$('financeNote').disabled=on;$('financeSubmit').disabled=busy;}
  function resize(){const field=$('financeNote');field.style.height='auto';field.style.height=field.scrollHeight+'px';}
  function open(nextAction='credit',entry=null){
    if(!privileged()||busy)return;pending=null;try{pending=JSON.parse(sessionStorage.getItem(key())||'null');}catch{}
    if(pending){action=pending.action;source=pending.entry_id||null;}else{action=nextAction;source=entry?.id||null;}
    $('financeTitle').textContent={credit:'Mədaxil',debit:'Məxaric',reverse:'Əməliyyatı geri al'}[action];
    $('financeTarget').textContent=members.find(row=>String(row.telegram_id)===String(pending?.member_id||member))?.display_name||profile.display_name||'Əməkdaş';
    $('financeAmountLabel').hidden=action==='reverse';$('financeAmount').required=action!=='reverse';$('financeAmount').value=pending?.amount||'';$('financeNote').value=pending?.note||'';
    notice('financeFormNotice',pending?'Əvvəlki sorğunun nəticəsini eyni ID ilə yoxlayın.':'');lock(!!pending);modal.classList.add('show');resize();
  }
  function close(){if(!busy)modal.classList.remove('show');}
  $('financeForm').onsubmit=async event=>{
    event.preventDefault();if(busy||!privileged())return;
    if(!pending){const note=$('financeNote').value.trim(),amount=$('financeAmount').value.trim();if(!note||(action!=='reverse'&&!amount)){notice('financeFormNotice','Məbləğ və səbəb lazımdır.');return;}
      pending={action,member_id:member,request_id:crypto.randomUUID(),expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id,note,...(action==='reverse'?{entry_id:source}:{amount})};}
    const savedKey=key(),version=epoch,data=pending;
    try{sessionStorage.setItem(savedKey,JSON.stringify(data));}catch{notice('financeFormNotice','Brauzer yaddaşını aktiv edin.');return;}
    busy=true;lock(true);$('financeSubmit').innerHTML='<i class="spinner"></i>Gözləyin…';buttons();
    try{await api('/api/platform/finance',data);sessionStorage.removeItem(savedKey);if(version!==epoch)return;
      pending=null;busy=false;close();await load();
    }catch(error){if(version!==epoch)return;notice('financeFormNotice',error.message);if(error.status&&error.status<500){sessionStorage.removeItem(savedKey);pending=null;lock(false);}}
    finally{if(version===epoch){busy=false;lock(!!pending);$('financeSubmit').textContent=pending?'Eyni sorğunu yoxla':'Təsdiq et';buttons();}}
  };
  async function apply(data){const next=JSON.stringify([data.member,data.capabilities]);if(next===scope)return;scope=next;epoch++;const version=epoch;
    profile=data.member;capabilities=data.capabilities||{};busy=loading=false;rows=[];members=[];member=profile.telegram_id;total=offset=0;pending=null;active=true;modal.classList.remove('show');
    $('financeEntries').innerHTML='';$('financeMember').innerHTML='';$('financeBalance').textContent='Balans: —';$('financeNote').value='';$('financeAmount').value='';$('financeTarget').textContent='';notice('financeNotice','');notice('financeFormNotice','');buttons();
    $('financeMember').hidden=!privileged();if(!capabilities.modules?.finance)return;
    if(privileged()){try{const body=await api('/api/platform/finance?members=1&limit=100');if(version!==epoch)return;members=body.members||[];
      $('financeMember').innerHTML=members.map(row=>`<option value="${esc(row.telegram_id)}">${esc(row.display_name)}${row.active?'':' (deaktiv)'}</option>`).join('');$('financeMember').value=String(member);
      const saved=JSON.parse(sessionStorage.getItem(key())||'null');if(saved&&String(saved.expected_tenant_id)===String(profile.tenant_id)&&String(saved.expected_user_id)===String(profile.telegram_id)&&['credit','debit','reverse'].includes(saved.action))open();
    }catch(error){if(version===epoch)notice('financeNotice',error.message);}}
    if(version===epoch&&$('view-finance').classList.contains('active'))load();
  }
  $('financeCredit').onclick=()=>open('credit');$('financeDebit').onclick=()=>open('debit');$('financeRefresh').onclick=()=>load();$('financePrev').onclick=()=>load(Math.max(0,offset-50));$('financeNext').onclick=()=>load(offset+50);
  $('financeMember').onchange=()=>{member=Number($('financeMember').value);load();};$('financeEntries').onclick=event=>{const button=event.target.closest('[data-finance-reverse]');if(button)open('reverse',rows.find(row=>row.id===button.dataset.financeReverse));};
  $('financeClose').onclick=close;$('financeCancel').onclick=close;$('financeNote').oninput=resize;nav.addEventListener('click',()=>load());
  document.addEventListener('tenant-profile',event=>apply(event.detail));const initial=epoch;api('/api/platform/me').then(data=>{if(initial===epoch)apply(data);}).catch(()=>{});
})();
