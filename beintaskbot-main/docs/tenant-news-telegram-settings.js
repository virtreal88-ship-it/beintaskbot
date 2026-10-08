/* Channel binding belongs to company owner; verification never posts a test message. */
(() => {
  const $=id=>document.getElementById(id);let profile,epoch=0,version='',busy=false;
  async function api(data){const response=await fetch('/api/platform/news/telegram/settings',{credentials:'same-origin',cache:'no-store',...(data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});const result=await response.json();if(!response.ok||!result.success)throw new Error(result.error||'Kanal ayarları alınmadı.');return result;}
  function disable(on){busy=on;$('tnChannelPanel')?.querySelectorAll('input,button').forEach(input=>input.disabled=on);}
  async function load(){if(busy)return;const current=epoch;disable(true);try{const result=await api();if(current!==epoch)return;version=result.updated_at;
    $('tnChannelAddress').value=result.chat_id;$('tnChannelEnabled').checked=result.enabled;$('tnChannelTimezone').value=result.timezone;$('tnChannelStart').value=result.start;$('tnChannelEnd').value=result.end;$('tnChannelInterval').value=result.interval_minutes;$('tnBotName').textContent=result.bot_username||'hazırda əlçatan deyil';
    $('tnChannelNotice').textContent=result.configured?`${result.title} · ${result.enabled?'Aktiv':'Dayandırılıb'}`:'Kanal qoşulmayıb.';
    }catch(error){if(current===epoch)$('tnChannelNotice').textContent=error.message;}finally{if(current===epoch)disable(false);}}
  async function save(verify){if(busy)return;const current=epoch;disable(true);$('tnChannelNotice').textContent=verify?'Kanal yoxlanılır…':'Dayandırılır…';
    try{const result=await api({channel:$('tnChannelAddress').value.trim(),enabled:verify&&$('tnChannelEnabled').checked,verify,
      timezone:$('tnChannelTimezone').value,start:$('tnChannelStart').value,end:$('tnChannelEnd').value,interval_minutes:Number($('tnChannelInterval').value),
      expected_updated_at:version,expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id});if(current!==epoch)return;version=result.updated_at;$('tnChannelAddress').value=result.chat_id;$('tnChannelEnabled').checked=result.enabled;
      $('tnChannelNotice').textContent=`${result.title} · Saxlanıldı. Kanal yoxlaması mesaj göndərmədi.`;
    }catch(error){if(current===epoch)$('tnChannelNotice').textContent=error.message;}finally{if(current===epoch)disable(false);}}
  function install(data){const next=data.member,identity=next.tenant_id+':'+next.telegram_id+':'+next.role;if(profile?.identity===identity)return;epoch++;profile={...next,identity};busy=false;version='';$('tnChannelPanel')?.remove();if(next.role!=='owner')return;
    $('view-settings').insertAdjacentHTML('beforeend',`<article class="panel" id="tnChannelPanel" style="margin-top:16px"><h2>Telegram xəbər kanalı</h2><p>Botu kanalda administrator edin və paylaşım hüququ verin. Şirkət sahibi də həmin kanalda administrator olmalıdır. Botun adı: <span id="tnBotName">profilinizdəki bot</span>. Kanal yalnız bu şirkətə bağlanır. Əvvəlki xəbərlər avtomatik göndərilmir.</p>
      <label>Kanal<input id="tnChannelAddress" placeholder="@kanal və ya -100…"></label><label>Saat qurşağı<input id="tnChannelTimezone" value="Asia/Baku"></label><label>Başlanğıc<input type="time" id="tnChannelStart" value="09:00"></label><label>Son vaxt (bu dəqiqə daxil)<input type="time" id="tnChannelEnd" value="19:00"></label><label>Minimum interval, dəqiqə<input type="number" id="tnChannelInterval" min="60" max="1440" value="60"></label>
      <label style="display:flex"><input id="tnChannelEnabled" type="checkbox" style="width:auto">Təsdiqlənmiş Telegram növbəsini aktiv et</label><p>Hər xəbər üçün «Telegram-a göndər» ayrıca təsdiqlənir. Naməlum nəticələr avtomatik təkrarlanmır.</p>
      <button class="outline" id="tnChannelLoad">Yenilə</button><button class="primary" id="tnChannelSave">Yoxla və saxla</button><button class="outline danger" id="tnChannelPause">Göndərişi dayandır</button><p id="tnChannelNotice" role="status"></p></article>`);
    $('tnChannelLoad').onclick=load;$('tnChannelSave').onclick=()=>save(true);$('tnChannelPause').onclick=()=>save(false);load();
  }
  document.addEventListener('tenant-profile',event=>install(event.detail));const initial=epoch;
  fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(initial===epoch&&data?.member)install(data);}).catch(()=>{});
})();
