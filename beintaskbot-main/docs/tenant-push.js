/* Browser opt-in only. Company permissions gate every delivered event. */
(() => {
  let profile, generation=0, readVersion=0, busy=false;
  const panel=document.createElement('article');panel.className='panel';
  panel.innerHTML='<h2>Push bildirişləri</h2><p>Bu şirkət üçün cihazı qoşun. Hansı hadisələrin gələcəyini şirkət sahibi seçir.</p><button class="outline push-connect">Bu cihazı qoş</button><div class="push-status" role="status" style="margin-top:12px"></div><div class="push-devices"></div>';
  document.querySelector('#view-profile .grid').append(panel);
  const button=panel.querySelector('.push-connect'),status=panel.querySelector('.push-status'),list=panel.querySelector('.push-devices');
  const supported=()=>window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  const identity=person=>String(person?.tenant_id)+':'+String(person?.telegram_id);
  const current=(person,version)=>version===generation && identity(profile)===identity(person);
  async function api(data,person) {
    const expected=person||profile;
    const response=await fetch('/api/platform/push/devices',{credentials:'same-origin',cache:'no-store',
      ...(data ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...data,expected_tenant_id:String(person.tenant_id),expected_user_id:String(person.telegram_id)})} : {})});
    const result=await response.json();
    if(!response.ok||!result.success)throw Error(result.error||'Push ayarları alınmadı.');
    if(result.tenant_id && (String(result.tenant_id)!==String(expected?.tenant_id)||String(result.user_id)!==String(expected?.telegram_id)))throw Error('Kabinet dəyişib. Səhifəni yeniləyin.');
    return result;
  }
  async function refresh() {
    const person=profile,version=generation;if(!person||busy)return;const read=++readVersion;
    try {
      const result=await api();if(!current(person,version)||read!==readVersion)return;
      list.replaceChildren();
      for(const device of result.devices||[]) {
        const row=document.createElement('div');row.className='member';
        const name=document.createElement('span');name.textContent=device.label||'Cihaz';
        const remove=document.createElement('button');remove.className='outline danger';remove.textContent='Ayır';
        remove.onclick=async()=>{if(busy)return;busy=true;readVersion++;remove.disabled=true;button.disabled=true;try{await api({action:'unsubscribe',device_id:device.device_id},person);if(current(person,version))status.textContent='Cihaz bu şirkətdən ayrıldı.';}catch(error){if(current(person,version))status.textContent=error.message;}finally{busy=false;button.disabled=!supported();await refresh();}};
        row.append(name,remove);list.append(row);
      }
      if(!result.delivery_enabled)status.textContent='Serverdə push açarları hazır deyil.';
      else if(!supported())status.textContent='Bu brauzer push dəstəkləmir. iPhone-da tətbiqi ana ekrana əlavə etmək lazım ola bilər.';
    }catch(error){if(current(person,version)&&read===readVersion)status.textContent=error.message;}
  }
  async function activeRegistration() {
    const reg=await navigator.serviceWorker.register('/app/push-sw.js',{scope:'/app/'});
    if(reg.active)return reg;
    const worker=reg.installing||reg.waiting;
    if(!worker)throw Error('Push xidməti başlamadı.');
    await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{worker.removeEventListener('statechange',changed);reject(Error('Push xidməti başlamadı. Yenidən yoxlayın.'));},20000);
      const changed=()=>{if(worker.state==='activated'){clearTimeout(timer);worker.removeEventListener('statechange',changed);resolve();}else if(worker.state==='redundant'){clearTimeout(timer);worker.removeEventListener('statechange',changed);reject(Error('Push xidməti başlamadı.'));}};
      worker.addEventListener('statechange',changed);changed();
    });
    return reg;
  }
  button.onclick=async()=>{
    if(busy||!profile||!supported())return;
    const person=profile,version=generation;busy=true;readVersion++;button.disabled=true;button.textContent='Qoşulur…';
    try {
      if(await Notification.requestPermission()!=='granted')throw Error('Brauzerdə bildiriş icazəsi verilməyib.');
      const config=await api();if(!current(person,version))return;
      if(!config.delivery_enabled||!config.public_key)throw Error('Serverdə push açarları hazır deyil.');
      const reg=await activeRegistration();if(!current(person,version))return;
      const padded=config.public_key.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-config.public_key.length%4)%4);
      const key=Uint8Array.from(atob(padded),char=>char.charCodeAt(0));
      const sub=await reg.pushManager.getSubscription()||await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
      if(!current(person,version))return;
      await api({action:'subscribe',subscription:sub.toJSON(),label:String(navigator.platform||'Brauzer').slice(0,60)},person);
      if(current(person,version))status.textContent='Cihaz qoşuldu. Bildiriş növləri əməkdaş ayarlarından seçilir.';
    }catch(error){if(current(person,version))status.textContent=error.message;}
    finally{busy=false;button.disabled=!supported();button.textContent='Bu cihazı qoş';await refresh();}
  };
  window.tenantPushLogout=async()=>{
    if(busy)throw Error('Push əməliyyatı bitsin, sonra çıxış edin.');
    if(!('serviceWorker' in navigator))return;
    const reg=await navigator.serviceWorker.getRegistration('/app/');
    if(!reg||new URL(reg.scope).pathname!=='/app/')return;
    const sub=await reg.pushManager.getSubscription();if(sub)await sub.unsubscribe();
  };
  function apply(data){profile=data.member;generation++;list.replaceChildren();status.textContent='';button.disabled=busy||!supported();refresh();}
  let received=false;
  document.addEventListener('tenant-profile',event=>{received=true;apply(event.detail);});
  fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(response=>response.json()).then(data=>{if(data.success&&!received)apply(data);}).catch(()=>{});
})();
