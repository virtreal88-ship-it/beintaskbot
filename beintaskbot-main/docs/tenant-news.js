/* Company review only. Explicit save+publish, scoped responses and exact retries. */
(() => {
  const $=id=>document.getElementById(id),esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let profile,epoch=0,rows=[],after=null,loading=false,sending=false,pending=null,channel=null;
  document.head.insertAdjacentHTML('beforeend',`<style>.tn-toolbar,.tn-actions{display:flex;gap:9px;flex-wrap:wrap;margin:14px 0}.tn-card{margin:14px 0;overflow-wrap:anywhere}.tn-card h2{font-weight:750}.tn-card .tn-body{white-space:pre-wrap;margin:12px 0}.tn-meta{font-size:12px;color:var(--muted)}#tnModal .modal-card{max-height:85dvh;overflow:auto;padding-bottom:calc(24px + env(safe-area-inset-bottom))}#tnModal textarea{width:100%;font:inherit;padding:10px;border:1px solid var(--line);border-radius:9px;resize:vertical;min-height:48px}#tnModal .tn-actions{position:sticky;bottom:0;background:white;padding:12px 0}</style>`);
  document.querySelector('.nav').insertAdjacentHTML('beforeend','<button id="tnNav" data-view="news" hidden style="display:none"><span>▤</span> Xəbərlər</button>');
  document.querySelector('main').insertAdjacentHTML('beforeend',`<section class="view" id="view-news"><div class="top"><h1>Xəbərlər</h1></div>
    <p>Saytda yalnız təsdiqlədiyiniz xəbər dərc olunur. Telegram üçün ayrıca seçim və yoxlanmış kanal lazımdır.</p><a id="tnPublic" target="_blank" rel="noopener">Şirkətin xəbər səhifəsi ↗</a>
    <div class="tn-toolbar"><select class="search" id="tnStatus"><option value="pending">Təsdiq gözləyir</option><option value="published">Dərc olunub</option><option value="rejected">İmtina edilib</option></select><button class="refresh" id="tnRefresh">Yenilə</button><button class="primary" id="tnCreate">+ Xəbər</button></div>
    <p id="tnNotice" role="status"></p><button class="outline" id="tnRecover" hidden>Yarımçıq sorğunu yoxla</button><div id="tnRows"></div><button class="outline" id="tnMore" hidden>Daha çox</button></section>
    <div class="modal" id="tnModal"><div class="modal-card panel"><div class="modal-head"><h2>Xəbəri hazırla</h2><button class="close" id="tnClose" aria-label="Bağla">×</button></div>
    <p>Yalnız başlıq və qısa mətn. Mənbə fraqmentini yoxlayın; şəxsi məlumatları dərc etməyin.</p><div id="tnFields"></div><p id="tnMessage" role="status"></p><div class="tn-actions"><button class="primary" id="tnPublish">Saxla və dərc et</button><button class="outline" id="tnCancel">Bağla</button></div></div></div>`);
  const key=()=>`tenant-news-command:${profile.tenant_id}:${profile.telegram_id}`;
  function date(v){const d=new Date(v);return Number.isNaN(d.getTime())?'—':d.toLocaleString('en-GB',{timeZone:'Asia/Baku',hour12:false});}
  async function api(params='',data){const response=await fetch('/api/platform/news'+params,{credentials:'same-origin',cache:'no-store',...(data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});
    const result=await response.json();if(!response.ok||!result.success){const error=new Error(result.error||'Xəbər sorğusu alınmadı.');error.status=response.status;throw error;}return result;}
  function render(){ $('tnMore').hidden=!after;$('tnRecover').hidden=!pending;
    $('tnRows').innerHTML=rows.map(row=>`<article class="panel tn-card" data-news="${esc(row.id)}"><div class="tn-meta">${esc(row.project_name)}${row.identifier?' · '+esc(row.identifier):''} · ${date(row.published_at||row.created_at)}</div>
      <h2>${esc(row.title)}</h2><div class="tn-body">${esc(row.summary)}</div><div class="tn-meta">${esc(row.url)}${row.classification?.category?' · AI: '+esc(row.classification.category):' · Mənbə fraqmenti — əl ilə yoxlayın'}</div><div class="tn-meta">Telegram: ${esc(({pending:'Növbədə',sending:'Göndərilir',sent:'Göndərilib',unknown:'Nəticə naməlum — kanalı yoxlayın, təkrar göndərilmir',blocked:'Bloklanıb',cancelled:'Ləğv edilib',expired:'Müddəti bitib'})[row.telegram_status]||'Göndərilməyib')}</div>
      ${row.status==='pending'?'<div class="tn-actions"><button class="outline" data-edit>Yoxla və dərc et</button><button class="outline danger" data-reject>İmtina</button></div>':row.status==='published'?`<div class="tn-actions">${channel?.enabled&&![ 'sent','sending','unknown','pending','expired' ].includes(row.telegram_status)?'<button class="outline" data-telegram>Telegram-a göndər</button>':''}${['pending','blocked'].includes(row.telegram_status)?'<button class="outline danger" data-cancel-telegram>Növbədən çıxar</button>':''}</div>`:''}</article>`).join('')||'<div class="empty">Xəbər yoxdur.</div>';
  }
  async function load(more=false){if(!profile||loading)return;const current=epoch;loading=true;$('tnNotice').textContent='Yüklənir…';$('tnMore').disabled=$('tnRefresh').disabled=true;
    try{const result=await api('?status='+$('tnStatus').value+(more&&after?'&after='+encodeURIComponent(after):''));if(current!==epoch)return;
      if(String(result.tenant_id)!==String(profile.tenant_id)||String(result.user_id)!==String(profile.telegram_id))throw new Error('Kabinet dəyişib.');
      try{const config=await api('/telegram');if(current!==epoch)return;channel=config;}catch{if(current!==epoch)return;channel=null;}
      rows=more?[...new Map([...rows,...result.news].map(row=>[row.id,row])).values()]:result.news;after=result.after;render();$('tnNotice').textContent='';$('tnNav').hidden=false;$('tnNav').style.display='';
      if(new URL(location.href).searchParams.get('view')==='news'){document.querySelectorAll('.view').forEach(view=>view.classList.toggle('active',view.id==='view-news'));document.querySelectorAll('.nav button').forEach(button=>button.classList.toggle('active',button.id==='tnNav'));}
    }catch(error){if(current===epoch){rows=[];after=null;render();$('tnNotice').textContent=error.message;if(error.status===403){$('tnNav').hidden=true;$('tnNav').style.display='none';close();}}}
    finally{if(current===epoch){loading=false;$('tnMore').disabled=$('tnRefresh').disabled=false;}}}
  function close(){if(!sending)$('tnModal').classList.remove('show');}
  function grow(input){input.style.height='auto';input.style.height=input.scrollHeight+'px';}
  function recover(){if(!pending)return;const current=epoch;$('tnFields').innerHTML='<p>Eyni məlumat və eyni sorğu ID-si ilə nəticəni yoxlayın. Yeni xəbər yaradılmır.</p>';$('tnMessage').textContent='';$('tnPublish').textContent='Eyni sorğunu yoxla';$('tnPublish').onclick=()=>send(pending,current);$('tnModal').classList.add('show');}
  function edit(row){if(pending){recover();return;}const current=epoch;$('tnFields').innerHTML=`<label>Layihə<input id="tnProject" maxlength="100" value="${esc(row?.project_name||'')}"></label>
    <label>Başlıq<textarea id="tnTitle" maxlength="255">${esc(row?.title||'')}</textarea></label><label>Qısa mətn<textarea id="tnSummary" maxlength="1200">${esc(row?.summary||'')}</textarea></label>
    <label>URL və ya telefon nömrəsi<input id="tnUrl" maxlength="2048" value="${esc(row?.url||'')}" placeholder="Boş qala bilər"></label>
    <label style="display:flex"><input id="tnConfirm" type="checkbox" style="width:auto">Bu mətnin saytda hamıya açıq dərc olunmasını təsdiqləyirəm</label>
    ${channel?.enabled?`<label style="display:flex"><input id="tnTelegramConfirm" type="checkbox" style="width:auto">${esc(channel.title)} kanalına da növbəyə əlavə et</label>`:'<p>Telegram kanalı aktiv deyil; yalnız saytda dərc olunur.</p>'}`;
    $('tnMessage').textContent='';$('tnPublish').textContent='Saxla və dərc et';$('tnModal').classList.add('show');
    for(const input of [$('tnTitle'),$('tnSummary')]){input.oninput=()=>grow(input);grow(input);}
    $('tnPublish').onclick=()=>{if(current!==epoch||!$('tnConfirm').checked){$('tnMessage').textContent='Açıq dərc olunmasını təsdiqləyin.';return;}
      if(![$('tnTitle'),$('tnSummary'),$('tnProject')].every(input=>input.value.trim())){$('tnMessage').textContent='Başlıq, qısa mətn və layihə məcburidir.';return;}
      const data={action:row?'publish':'create_publish',id:row?.id,request_id:crypto.randomUUID(),expected_updated_at:row?.updated_at,
        title:$('tnTitle').value,summary:$('tnSummary').value,project_name:$('tnProject').value,url:$('tnUrl').value,confirm_publication:true,
        confirm_telegram:$('tnTelegramConfirm')?.checked===true,channel_version:channel?.binding_version,
        expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id};send(data,current);};
  }
  async function send(data,current=epoch){if(sending||current!==epoch)return;try{sessionStorage.setItem(key(),JSON.stringify(data));pending=data;}catch{$('tnMessage').textContent='Sorğunu təhlükəsiz saxlamaq alınmadı.';return;}
    sending=true;$('tnStatus').disabled=true;$('tnPublish').disabled=true;$('tnPublish').textContent='Göndərilir…';$('tnMessage').textContent='';
    try{const result=await api(data.telegram_operation?'/telegram':'',data);if(current!==epoch)return;
      if(String(result.tenant_id)!==String(profile.tenant_id)||String(result.user_id)!==String(profile.telegram_id))throw new Error('Kabinet dəyişib.');
      sessionStorage.removeItem(key());pending=null;sending=false;close();await load();
    }catch(error){if(current===epoch){if(error.status&&error.status<500){sessionStorage.removeItem(key());pending=null;$('tnMessage').textContent=error.message;$('tnNotice').textContent=error.message;
      if(!$('tnTitle')||error.status===403){sending=false;close();}if(error.status===403){rows=[];after=null;$('tnNav').hidden=true;$('tnNav').style.display='none';}}
      else{recover();$('tnMessage').textContent='Nəticə məlum deyil. Eyni sorğunu yoxlayın.';}}}
    finally{if(current===epoch){sending=false;$('tnStatus').disabled=false;$('tnPublish').disabled=false;$('tnPublish').textContent=pending?'Eyni sorğunu yoxla':'Saxla və dərc et';render();}}}
  function install(data){const next=data.member;const identity=JSON.stringify([next.tenant_id,next.telegram_id,next.role,next.permissions,next.active,data.capabilities]);if(profile?.identity===identity)return;
    epoch++;profile={...next,identity};rows=[];after=null;channel=null;loading=sending=false;pending=null;$('tnStatus').disabled=$('tnPublish').disabled=false;$('tnModal').classList.remove('show');$('tnNav').hidden=true;$('tnNav').style.display='none';
    try{const value=JSON.parse(sessionStorage.getItem(key())||'null');if(value&&String(value.expected_tenant_id)===String(next.tenant_id)&&String(value.expected_user_id)===String(next.telegram_id))pending=value;}catch{}
    $('tnPublic').href='/updates/'+encodeURIComponent(next.tenant_id);render();load();}
  $('tnNav').onclick=()=>{document.querySelectorAll('.view').forEach(view=>view.classList.toggle('active',view.id==='view-news'));document.querySelectorAll('.nav button').forEach(button=>button.classList.toggle('active',button.id==='tnNav'));const url=new URL(location.href);url.searchParams.set('view','news');history.replaceState(null,'',url);load();};
  $('tnRefresh').onclick=()=>load();$('tnMore').onclick=()=>load(true);$('tnCreate').onclick=()=>edit();$('tnRecover').onclick=recover;
  $('tnStatus').onchange=()=>{if(sending)return;close();epoch++;loading=false;rows=[];after=null;render();load();};$('tnClose').onclick=$('tnCancel').onclick=close;
  $('tnRows').onclick=event=>{const card=event.target.closest('[data-news]'),row=rows.find(item=>item.id===card?.dataset.news);if(!row)return;
    if(event.target.closest('[data-edit]'))edit(row);
    if(event.target.closest('[data-reject]')&&!pending&&confirm('Xəbərdən imtina edilsin?'))send({action:'reject',id:row.id,expected_updated_at:row.updated_at,expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id});};
  $('tnRows').addEventListener('click',event=>{const card=event.target.closest('[data-news]'),row=rows.find(item=>item.id===card?.dataset.news);if(!row||pending||sending)return;
    const enqueue=event.target.closest('[data-telegram]'),cancel=event.target.closest('[data-cancel-telegram]');if(!enqueue&&!cancel)return;
    if(confirm(enqueue?`Xəbər «${channel?.title||''}» kanalına göndərilsin?`:'Telegram növbəsindən çıxarılsın?'))send({telegram_operation:true,action:enqueue?'enqueue':'cancel',id:row.id,expected_updated_at:row.updated_at,confirm_telegram:!!enqueue,channel_version:channel?.binding_version,expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id});
  });
  document.addEventListener('tenant-profile',event=>install(event.detail));const initial=epoch;
  fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(initial===epoch&&data?.member)install(data);}).catch(()=>{});
})();
