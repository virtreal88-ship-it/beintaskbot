/* Server-issued capabilities for company-wide connections. No role inference. */
(() => {
  const row = (data, provider) => (data?.integrations || []).find(item => item.provider === (provider === 'openai' ? 'chat_ai' : provider));
  const scoped = (data, provider) => !!row(data, provider)?.management_actions;
  const can = (data, provider, action) => row(data, provider)?.management_actions?.[action] === true;
  const connected = (data, provider) => ['connected', 'configured', 'awaiting_oauth'].includes(row(data, provider)?.status);
  function command(data, provider, payload, action) {
    if (!scoped(data, provider)) return payload;
    if (!can(data, provider, action)) throw new Error('Bu əməliyyat üçün icazə yoxdur.');
    const impact = action === 'delete' || (action === 'update' && (payload.action === 'reconnect_channel' || (provider === 'connect' && Array.isArray(payload.channel_ids) && payload.channel_ids.length > 0) || ['api_key','access_token','token','remove_key'].some(key => payload[key])));
    if (impact && !window.confirm('Bu dəyişiklik bütün şirkətin bağlantısına təsir edəcək. Davam edilsin?')) throw new Error('Əməliyyat ləğv edildi.');
    return {...payload, expected_tenant_id:data.member.tenant_id, expected_user_id:data.member.telegram_id, ...(impact ? {confirm_company_impact:true} : {})};
  }
  function record(data, provider, result) {
    const item=row(data,provider), value=result?.integration||result;
    if(!item||!value)return;
    if(value.updated_at!==undefined)item.updated_at=value.updated_at;
    if(value.status)item.status=value.status;
    else if(value.configured!==undefined)item.status=value.configured?'configured':'not_connected';
    else if(value.connected!==undefined)item.status=value.connected?'connected':'not_connected';
  }
  window.tenantSharedIntegrations = {row, scoped, can, connected, command, record};
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  for (const provider of ['beinsystems','connect']) {
    let profile, epoch=0;
    const id='sharedIntegration-'+provider;
    async function request(path, payload) {
      const response=await fetch(path,{credentials:'same-origin',cache:'no-store',...(payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{})});
      const result=await response.json();if(!response.ok||result.success===false)throw new Error(result.error||'Əməliyyat baş tutmadı.');return result;
    }
    function install(data) {
      if (!scoped(data,provider)) return;
      profile=data; const current=++epoch;
      const host=$('integrationSettingsHost');if(!host)return;
      const panel=document.createElement('article');panel.id=id;panel.className='panel integration-modal-panel';host.append(panel);
      const actions=row(data,provider).management_actions;
      const write=actions[connected(data,provider)?'update':'create'];
      panel.innerHTML=`<h2>${provider==='connect'?'Connect WhatsApp':'BeinSystems'}</h2><p>Şirkətin ortaq bağlantısı · ${esc(row(data,provider).status)}</p><p data-message role="status"></p>`;
      if(write)panel.insertAdjacentHTML('beforeend',provider==='connect'?'<label>Connect API açarı<input data-key type="password" autocomplete="new-password"></label>':'<label>API ünvanı<input data-url type="url"></label><label>Filial<input data-branch></label><label>API token<input data-key type="password" autocomplete="new-password"></label>');
      const message=panel.querySelector('[data-message]');
      const button=(label,action)=>{const b=document.createElement('button');b.type='button';b.className='outline';b.textContent=label;b.onclick=async()=>{if(current!==epoch)return;b.disabled=true;try{await action();}catch(error){if(current===epoch)message.textContent=error.message;}finally{if(current===epoch)b.disabled=false;}};panel.append(b);return b;};
      const post=async(payload,action,path='/api/platform/integrations/'+provider)=>{const body=command(profile,provider,payload,action);const result=await request(path,body);if(current!==epoch)return null;record(profile,provider,result);if(path.endsWith('/disconnect'))row(profile,provider).status='not_connected';message.textContent='Saxlanıldı.';if(result.qr&&result.qr.startsWith('data:image/')){const image=document.createElement('img');image.src=result.qr;image.alt='WhatsApp QR';image.style.maxWidth='240px';message.append(image);}return result;};
      if(provider==='beinsystems'){
        if(write)button('Tokeni saxla',async()=>{const key=panel.querySelector('[data-key]');await post({action:'connect',token:key.value,base_url:panel.querySelector('[data-url]').value,branch_id:panel.querySelector('[data-branch]').value},connected(profile,provider)?'update':'create');key.value='';});
        if(actions.view)button('Bağlantını yoxla',()=>post({action:'test'},'view'));
        if(actions.update)button('Əsas CRM kimi seç',()=>post({action:'primary',provider},'update'));
        if(actions.delete)button('Bağlantını ayır',()=>post({provider},'delete','/api/platform/integrations/disconnect'));
      } else {
        const list=document.createElement('div');panel.append(list);
        if(actions.delete&&!actions.view){
          panel.insertAdjacentHTML('beforeend','<p>Channel UUID ilə bir WhatsApp nömrəsini ayırın. Bu nömrənin bağlantısı bütün şirkət üçün dayandırılacaq; digər nömrələr dəyişməyəcək. Kanal siyahısını görmək icazəniz yoxdur.</p><label>Channel UUID<input data-disconnect-channel autocomplete="off" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"></label>');
          const disconnect=button('Bu nömrəni şirkətdən ayır',async()=>{
            const channel_id=panel.querySelector('[data-disconnect-channel]').value.trim();
            if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(channel_id))throw new Error('Düzgün Channel UUID daxil edin.');
            await post({action:'disconnect_channel',channel_id},'delete');
          });
          disconnect.dataset.disconnectById='true';
        }
        if(actions.view || write)button('Kanalları göstər',async()=>{
          const key=panel.querySelector('[data-key]')?.value||'';
          const result=await post({action:'channels',api_key:key},key?(connected(profile,provider)?'update':'create'):'view');if(!result)return;
          list.replaceChildren();
          for(const channel of result.channels||[]){
            const entry=document.createElement('div');entry.textContent=`${channel.name||channel.id} · ${channel.status||''}`;list.append(entry);
            const actionButton=(label,action,scope)=>{if(!actions[scope])return;const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=async()=>{b.disabled=true;try{if(current!==epoch)return;await post({action,channel_id:channel.id},scope);}catch(e){if(current===epoch)message.textContent=e.message;}finally{if(current===epoch)b.disabled=false;}};entry.append(b);};
            if(channel.active || channel.linked){actionButton('Status','channel_status','view');if(!channel.phoneConflict)actionButton('Yenidən qoş','reconnect_channel','update');if(channel.active)actionButton('Ayır','disconnect_channel','delete');}
            else if(write&&!channel.phoneConflict){const choice=document.createElement('input');choice.type='checkbox';choice.value=channel.id;entry.prepend(choice);}
          }
        });
        if(write)button('Seçilmiş nömrələri qoş',async()=>{const key=panel.querySelector('[data-key]');const channel_ids=[...list.querySelectorAll('input:checked')].map(input=>input.value);if(!channel_ids.length)throw new Error('Kanal seçin.');await post({api_key:key.value,channel_ids},connected(profile,provider)?'update':'create');key.value='';});
      }
    }
    window.tenantViewDemand.watch({provider,editable:'#'+id,resetOnHide:true,activate:install,reset:()=>{epoch++;profile=null;$(id)?.remove();}});
  }
})();
