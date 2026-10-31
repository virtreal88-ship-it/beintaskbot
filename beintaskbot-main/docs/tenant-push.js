/* Browser opt-in only. Company permissions gate every delivered event. */
(() => {
  let profile, generation=0, readVersion=0, busy=false;
  window.tenantPushConnected=false;
  window.tenantPushConnectionState='loading';
  const words={
    title:['Push-уведомления','Push bildirişləri','Push notifications'],help:['Подключите устройство для уведомлений этой компании. Типы событий выбираются в настройках сотрудников.','Bu şirkət üçün cihazı qoşun. Hansı hadisələrin gələcəyini şirkət sahibi seçir.','Connect this device for company notifications. Event categories are set in employee settings.'],
    connect:['Включить уведомления','Bu cihazı qoş','Enable notifications'],connecting:['Подключаем…','Qoşulur…','Connecting…'],device:['Устройство','Cihaz','Device'],disconnect:['Отключить','Ayır','Disconnect'],
    failed:['Не удалось получить настройки уведомлений.','Push ayarları alınmadı.','Could not load notification settings.'],changed:['Компания изменилась. Обновите страницу.','Kabinet dəyişib. Səhifəni yeniləyin.','Workspace changed. Refresh this page.'],
    removed:['Устройство отключено от компании.','Cihaz bu şirkətdən ayrıldı.','Device disconnected from this company.'],server:['Серверные ключи Push не настроены.','Serverdə push açarları hazır deyil.','Server Push keys are not configured.'],
    unsupported:['Этот браузер не поддерживает Push. На iPhone добавьте приложение на домашний экран.','Bu brauzer push dəstəkləmir. iPhone-da tətbiqi ana ekrana əlavə etmək lazım ola bilər.','This browser does not support Push. On iPhone, add the app to the Home Screen.'],
    startFailed:['Служба уведомлений не запустилась. Повторите попытку.','Push xidməti başlamadı. Yenidən yoxlayın.','Notification service did not start. Try again.'],
    denied:['Уведомления запрещены. Откройте настройки сайта рядом с адресом Chrome и разрешите уведомления.','Brauzerdə bildiriş icazəsi verilməyib. Chrome ünvanının yanındakı sayt ayarlarında bildirişlərə icazə verin.','Notifications are blocked. Open site settings next to the Chrome address and allow notifications.'],
    connected:['Устройство подключено. Типы уведомлений выбираются в настройках сотрудников.','Cihaz qoşuldu. Bildiriş növləri əməkdaş ayarlarından seçilir.','Device connected. Select event categories in employee settings.'],
    wait:['Дождитесь завершения настройки уведомлений.','Push əməliyyatı bitsin, sonra çıxış edin.','Wait for notification setup to finish.'],checking:['Проверяем подключение…','Qoşulma yoxlanılır…','Checking connection…']
  };
  const text=key=>words[key][({ru:0,az:1,en:2})[window.tenantI18n?.language||document.documentElement?.lang]??1];
  const panel=document.createElement('article');panel.className='panel';
  panel.innerHTML=`<h2>${text('title')}</h2><p>${text('help')}</p><button class="outline push-connect">${text('connect')}</button><div class="push-status" role="status" style="margin-top:12px"></div><div class="push-devices"></div>`;
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
    if(!response.ok||!result.success)throw Error(result.error||text('failed'));
    if(result.tenant_id && (String(result.tenant_id)!==String(expected?.tenant_id)||String(result.user_id)!==String(expected?.telegram_id)))throw Error(text('changed'));
    return result;
  }
  async function browserDeviceIsConnected(devices) {
    try {
      if(!supported()||!window.crypto?.subtle)return false;
      const reg=await navigator.serviceWorker.getRegistration('/app/');
      if(!reg||new URL(reg.scope).pathname!=='/app/')return false;
      const sub=await reg.pushManager.getSubscription();if(!sub?.endpoint)return false;
      const digest=await window.crypto.subtle.digest('SHA-256',new TextEncoder().encode(sub.endpoint));
      const id=[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');
      return (devices||[]).some(device=>device.device_id===id);
    } catch { return false; }
  }
  async function refresh() {
    const person=profile,version=generation;if(!person||busy)return;const read=++readVersion;
    try {
      const result=await api();if(!current(person,version)||read!==readVersion)return;
      const connected=await browserDeviceIsConnected(result.devices);if(!current(person,version)||read!==readVersion)return;
      window.tenantPushConnected=connected;window.tenantPushConnectionState=connected?'connected':'disconnected';window.tenantOperatorUpdateBrowserNotice?.();
      list.replaceChildren();
      for(const device of result.devices||[]) {
        const row=document.createElement('div');row.className='member';
        const name=document.createElement('span');name.textContent=device.label||text('device');
        const remove=document.createElement('button');remove.className='outline danger';remove.textContent=text('disconnect');
        remove.onclick=async()=>{if(busy)return;busy=true;readVersion++;remove.disabled=true;button.disabled=true;try{await api({action:'unsubscribe',device_id:device.device_id},person);if(current(person,version))status.textContent=text('removed');}catch(error){if(current(person,version))status.textContent=error.message;}finally{busy=false;button.disabled=!supported();await refresh();}};
        row.append(name,remove);list.append(row);
      }
      if(!result.delivery_enabled)status.textContent=text('server');
      else if(!supported())status.textContent=text('unsupported');
    }catch(error){if(current(person,version)&&read===readVersion){window.tenantPushConnectionState='error';window.tenantOperatorUpdateBrowserNotice?.();status.textContent=error.message;}}
  }
  async function activeRegistration() {
    const reg=await navigator.serviceWorker.register('/app/push-sw.js',{scope:'/app/'});
    if(reg.active)return reg;
    const worker=reg.installing||reg.waiting;
    if(!worker)throw Error(text('startFailed'));
    await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{worker.removeEventListener('statechange',changed);reject(Error(text('startFailed')));},20000);
      const changed=()=>{if(worker.state==='activated'){clearTimeout(timer);worker.removeEventListener('statechange',changed);resolve();}else if(worker.state==='redundant'){clearTimeout(timer);worker.removeEventListener('statechange',changed);reject(Error(text('startFailed')));}};
      worker.addEventListener('statechange',changed);changed();
    });
    return reg;
  }
  button.onclick=async()=>{
    if(busy||!profile||!supported())return;
    const person=profile,version=generation;busy=true;readVersion++;button.disabled=true;button.textContent=text('connecting');
    try {
      if(Notification.permission==='denied'||await Notification.requestPermission()!=='granted')throw Error(text('denied'));
      const config=await api();if(!current(person,version))return;
      if(!config.delivery_enabled||!config.public_key)throw Error(text('server'));
      const reg=await activeRegistration();if(!current(person,version))return;
      const padded=config.public_key.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-config.public_key.length%4)%4);
      const key=Uint8Array.from(atob(padded),char=>char.charCodeAt(0));
      const sub=await reg.pushManager.getSubscription()||await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
      if(!current(person,version))return;
      await api({action:'subscribe',subscription:sub.toJSON(),label:String(navigator.platform||'Brauzer').slice(0,60)},person);
      if(current(person,version))status.textContent=text('connected');
    }catch(error){if(current(person,version))status.textContent=error.message;}
    finally{busy=false;button.disabled=!supported();button.textContent=text('connect');await refresh();}
  };
  window.tenantPushEnable=async()=>{await button.onclick();return {connected:window.tenantPushConnected===true,message:status.textContent};};
  document.addEventListener('tenant-language',()=>{button.textContent=text('connect');const title=panel.querySelector('h2'),help=panel.querySelector('p');if(title)title.textContent=text('title');if(help)help.textContent=text('help');});
  window.tenantPushLogout=async()=>{
    if(busy)throw Error(text('wait'));
    if(!('serviceWorker' in navigator))return;
    const reg=await navigator.serviceWorker.getRegistration('/app/');
    if(!reg||new URL(reg.scope).pathname!=='/app/')return;
    const sub=await reg.pushManager.getSubscription();if(sub)await sub.unsubscribe();
  };
  function apply(data){profile=data.member;generation++;window.tenantPushConnected=false;window.tenantPushConnectionState='loading';window.tenantOperatorUpdateBrowserNotice?.();list.replaceChildren();status.textContent='';button.disabled=busy||!supported();refresh();}
  let received=false;
  document.addEventListener('tenant-profile',event=>{received=true;apply(event.detail);});
  fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(response=>response.json()).then(data=>{if(data.success&&!received)apply(data);}).catch(()=>{});
})();
