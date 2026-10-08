/* Tenant-only queue. No polling, no legacy API, no payout mutations. */
(() => {
  const $=id=>document.getElementById(id), view=$('view-hot_orders');
  const nav=document.querySelector('[data-view="hot_orders"]');
  const esc=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let profile, capabilities={}, scope='', epoch=0, offset=0, total=0, loading=false, busy=false, rows=[], pending=null;
  const PAGE=50, labels={open:'Açıq',claimed:'Qəbul edilib',cancelled:'Ləğv edilib'};
  let linkOpened=false;
  const key=person=>`crm-hot-order:${person.tenant_id}:${person.telegram_id}`;
  const notice=(id,text)=>{$(id).textContent=text || '';$(id).classList.toggle('show',!!text);};
  async function api(url,data) {
    const response=await fetch(url,{credentials:'same-origin',cache:'no-store',...(data ?
      {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)} : {})});
    const body=await response.json();
    if(!response.ok || !body.success) {const error=new Error(body.error || 'Sorğu alınmadı.');error.status=response.status;throw error;}
    return body;
  }
  const style=document.createElement('style');
  style.textContent=`#view-hot_orders .top{flex-wrap:wrap}.hot-buttons,.hot-pages,.hot-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.hot-list{display:grid;gap:14px}.hot-card{padding:16px;background:#fff;border:1px solid var(--line);border-radius:15px;min-width:0}.hot-card header{display:flex;gap:10px;justify-content:space-between;align-items:start}.hot-card h2{font-size:18px;margin:0;overflow-wrap:anywhere}.hot-text{white-space:pre-wrap;overflow-wrap:anywhere;margin:12px 0}.hot-details{display:grid;gap:5px;color:var(--muted);font-size:13px;overflow-wrap:anywhere}.hot-pages{justify-content:center;margin-top:16px}.hot-actions{margin-top:14px}.hot-urgent{color:var(--danger);background:#fff1ef}#hotOrderModal{z-index:30}#hotOrderModal .modal-card{max-height:calc(100dvh - 32px);overflow:auto;padding-bottom:max(22px,env(safe-area-inset-bottom))}#hotOrderModal label{display:grid;gap:6px;margin:12px 0}#hotOrderModal input,#hotOrderModal select,#hotOrderModal textarea{width:100%;font:inherit;border:1px solid var(--line);border-radius:9px;padding:10px}#hotOrderModal textarea{resize:none;min-height:100px}#hotOrderModal .hot-footer{position:sticky;bottom:-22px;background:#fff;display:flex;gap:10px;padding:14px 0}#hotOrderModal .spinner,.hot-actions .spinner{display:inline-block}`;
  document.head.append(style);
  const modal=document.createElement('div');modal.id='hotOrderModal';modal.className='modal';
  modal.innerHTML=`<form class="modal-card" id="hotOrderForm"><div class="modal-head"><h2>Yeni isti sifariş</h2><button class="close" type="button" id="hotOrderClose" aria-label="Bağla">×</button></div><label>Xidmət<select id="hotService" required></select></label><label>Müştəri<input id="hotClient" maxlength="120" required></label><label>Telefon<input id="hotPhone" maxlength="80" type="tel"></label><label>Ünvan<input id="hotAddress" maxlength="500"></label><label>Prioritet<select id="hotPriority"><option value="normal">Normal</option><option value="urgent">Təcili</option></select></label><label>Sifariş<textarea id="hotDescription" maxlength="8000" required></textarea></label><div class="notice" role="status" id="hotCreateNotice"></div><div class="hot-footer"><button class="primary" id="hotSubmit" type="submit">Yarat</button><button class="outline" id="hotCancel" type="button">Bağla</button></div></form>`;
  document.body.append(modal);
  function resize() {const field=$('hotDescription');field.style.height='auto';field.style.height=field.scrollHeight+'px';}
  function lock(on) {['hotService','hotClient','hotPhone','hotAddress','hotPriority','hotDescription'].forEach(id=>$(id).disabled=on);$('hotSubmit').disabled=busy;}
  function pages() {
    $('hotOrderPrev').disabled=loading || busy || offset===0;
    $('hotOrderNext').disabled=loading || busy || offset+PAGE>=total;
    $('hotOrderPage').textContent=total ? `${offset+1}–${Math.min(offset+rows.length,total)} / ${total}` : '0';
    $('hotOrderCount').textContent=`${total} sifariş`;
  }
  function date(value) {const d=new Date(value);return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('az-AZ',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});}
  function render() {
    const action=(row,type,label)=>row.actions?.[type] ? `<button class="outline ${type==='cancel' ? 'danger' : ''}" data-hot-action="${type}" data-hot-id="${esc(row.id)}" ${busy ? 'disabled' : ''}>${label}</button>` : '';
    $('hotOrderList').innerHTML=rows.map(row=>`<article class="hot-card"><header><h2>${esc(row.client_name)}</h2><span class="badge">${esc(labels[row.status] || row.status)}</span></header><div class="hot-actions"><span class="badge">${esc(row.service_name || row.service_id)}</span>${row.priority==='urgent' ? '<span class="badge hot-urgent">Təcili</span>' : ''}</div><p class="hot-text">${esc(row.description)}</p><div class="hot-details">${row.phone ? `<span>Telefon: ${esc(row.phone)}</span>` : ''}${row.address ? `<span>Ünvan: ${esc(row.address)}</span>` : ''}<span>Yaradılıb: ${esc(date(row.created_at))}</span>${row.claimed_by ? `<span>İcraçı: ${String(row.claimed_by)===String(profile.telegram_id) ? 'Mən' : 'Əməkdaş #'+esc(row.claimed_by)}</span>` : ''}</div><div class="hot-actions">${action(row,'claim','Qəbul et')}${action(row,'release','Geri qaytar')}${action(row,'cancel','Ləğv et')}</div></article>`).join('') || '<div class="panel empty">Sifariş yoxdur.</div>';
    pages();
  }
  async function load(target=0) {
    if(!capabilities.modules?.hot_orders || loading || busy) return;
    const version=epoch;loading=true;$('hotOrderRefresh').disabled=true;$('hotOrderRefresh').classList.add('loading');pages();notice('hotOrderNotice','');
    try {
      const result=await api(`/api/platform/hot-orders?limit=${PAGE}&offset=${target}`);
      if(version!==epoch) return;
      rows=result.orders || [];total=result.total || 0;offset=target;
      if(offset>0 && rows.length===0) {loading=false;await load(Math.max(0,Math.floor(Math.max(0,total-1)/PAGE)*PAGE));return;}
      render();
    } catch(error) {if(version===epoch) notice('hotOrderNotice',error.message);}
    finally {if(version===epoch) {loading=false;$('hotOrderRefresh').disabled=false;$('hotOrderRefresh').classList.remove('loading');pages();}}
  }
  function close() {if(!busy) modal.classList.remove('show');}
  function open() {
    if(busy || !capabilities.hot_orders?.can_create) return;
    $('hotOrderForm').reset();pending=null;
    try {pending=JSON.parse(sessionStorage.getItem(key(profile)) || 'null');} catch {}
    $('hotService').innerHTML=(capabilities.hot_orders?.services || []).map(service=>`<option value="${esc(service.id)}">${esc(service.name)}</option>`).join('');
    if(pending) {
      if(!(capabilities.hot_orders.services || []).some(service=>service.id===pending.service_id)) $('hotService').insertAdjacentHTML('beforeend',`<option value="${esc(pending.service_id)}">Əvvəlki xidmət</option>`);
      for(const [id,field] of Object.entries({hotService:'service_id',hotClient:'client_name',hotPhone:'phone',hotAddress:'address',hotDescription:'description',hotPriority:'priority'})) $(id).value=pending[field];
    }
    lock(!!pending);$('hotSubmit').textContent=pending ? 'Eyni sorğunu yoxla' : 'Yarat';notice('hotCreateNotice',pending ? 'Əvvəlki sorğunun nəticəsini yoxlayın. Yeni sifariş yaratmayın.' : '');modal.classList.add('show');resize();
  }
  $('hotOrderForm').onsubmit=async event=>{
    event.preventDefault();if(busy || !capabilities.hot_orders?.can_create) return;
    if(!pending) {
      pending={request_id:crypto.randomUUID(),action:'create',expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id,
        service_id:$('hotService').value,client_name:$('hotClient').value.trim(),phone:$('hotPhone').value.trim(),address:$('hotAddress').value.trim(),description:$('hotDescription').value.trim(),priority:$('hotPriority').value};
      if(!pending.service_id || !pending.client_name || !pending.description) {pending=null;notice('hotCreateNotice','Müştəri, xidmət və mətn sahələrini doldurun.');return;}
    }
    const version=epoch, savedKey=key(profile), payload=pending;
    try {sessionStorage.setItem(savedKey,JSON.stringify(payload));} catch {notice('hotCreateNotice','Sorğunu təhlükəsiz saxlamaq alınmadı. Brauzer yaddaşını aktiv edin.');return;}
    busy=true;lock(true);$('hotSubmit').innerHTML='<i class="spinner"></i>Yaradılır…';
    try {
      await api('/api/platform/hot-orders',payload);sessionStorage.removeItem(savedKey);
      if(version!==epoch) return;
      pending=null;busy=false;close();await load(0);
    } catch(error) {
      if(version!==epoch) return;
      notice('hotCreateNotice',error.message);
      if(error.status && error.status<500) {sessionStorage.removeItem(savedKey);pending=null;lock(false);}
    } finally {if(version===epoch) {busy=false;lock(!!pending);$('hotSubmit').textContent=pending ? 'Eyni sorğunu yoxla' : 'Yarat';}}
  };
  $('hotOrderList').onclick=async event=>{
    const button=event.target.closest('[data-hot-action]');if(!button || busy || loading) return;
    const action=button.dataset.hotAction;if(action!=='claim' && !confirm(action==='cancel' ? 'Sifariş ləğv edilsin?' : 'Sifariş geri qaytarılsın?')) return;
    const version=epoch;busy=true;const text=button.textContent;button.disabled=true;button.innerHTML='<i class="spinner"></i>Gözləyin…';pages();
    try {
      await api('/api/platform/hot-orders',{action,order_id:button.dataset.hotId,expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id});
      if(version!==epoch) return;busy=false;await load(offset);
    } catch(error) {if(version===epoch) notice('hotOrderNotice',error.message+' Siyahını yeniləyin.');}
    finally {if(version===epoch) {busy=false;button.disabled=false;button.textContent=text;pages();}}
  };
  function apply(data) {
    const nextScope=JSON.stringify([data.member.tenant_id,data.member.telegram_id,data.member.role,data.member.active,data.member.permissions,data.capabilities?.modules?.hot_orders,data.capabilities?.hot_orders]);
    if(nextScope!==scope) {
      scope=nextScope;epoch++;rows=[];offset=total=0;loading=busy=false;pending=null;modal.classList.remove('show');
      $('hotOrderForm').reset();$('hotService').innerHTML='';lock(false);notice('hotCreateNotice','');
      $('hotOrderList').innerHTML='';$('hotOrderRefresh').disabled=false;$('hotOrderRefresh').classList.remove('loading');notice('hotOrderNotice','');
    }
    profile=data.member;capabilities=data.capabilities || {};
    $('hotOrderCreate').hidden=!capabilities.modules?.hot_orders || !capabilities.hot_orders?.can_create;
    $('hotOrderCreate').style.display=$('hotOrderCreate').hidden ? 'none' : '';pages();
    if(!linkOpened && capabilities.modules?.hot_orders && new URLSearchParams(window.location?.search || '').get('view')==='hot_orders') {linkOpened=true;nav.click();}
    if(view.classList.contains('active') && capabilities.modules?.hot_orders) load(0);
  }
  $('hotOrderCreate').onclick=open;$('hotOrderClose').onclick=close;$('hotCancel').onclick=close;$('hotDescription').oninput=resize;
  // Backdrop clicks never discard a draft. Close only through explicit buttons.
  $('hotOrderRefresh').onclick=()=>load(0);$('hotOrderPrev').onclick=()=>load(Math.max(0,offset-PAGE));$('hotOrderNext').onclick=()=>load(offset+PAGE);
  nav.addEventListener('click',()=>load(0));
  document.addEventListener('tenant-profile',event=>apply(event.detail));
  const initialEpoch=epoch;api('/api/platform/me').then(data=>{if(epoch===initialEpoch) apply(data);}).catch(()=>{});
})();
