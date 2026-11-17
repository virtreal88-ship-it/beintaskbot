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
        const labels={az:{name:'Ad',number:'Nömrə',pipeline:'Satış xətti',status:'Status',add:'+ Nömrə əlavə et',empty:'Qoşulmuş nömrə yoxdur.',loading:'Nömrələr yüklənir…',retry:'Yenidən yoxla',available:'Mövcud nömrələri yüklə',save:'Seçilmiş nömrələri qoş',conflict:'Nömrə konflikti',unknown:'Məlum deyil'},ru:{name:'Имя',number:'Номер',pipeline:'Воронка',status:'Статус',add:'+ Добавить номер',empty:'Нет подключённых номеров.',loading:'Загрузка номеров…',retry:'Повторить',available:'Загрузить доступные номера',save:'Подключить выбранные номера',conflict:'Конфликт номера',unknown:'Неизвестно'},en:{name:'Name',number:'Number',pipeline:'Pipeline',status:'Status',add:'+ Add number',empty:'No connected numbers.',loading:'Loading numbers…',retry:'Retry',available:'Load available numbers',save:'Connect selected numbers',conflict:'Phone conflict',unknown:'Unknown'}};
        const t=labels[window.tenantI18n?.language]||labels.az;
        let channels=[],adding=false,loading=false,readSerial=0;
        list.className='connect-numbers-wrap';
        const keyLabel=panel.querySelector('[data-key]')?.closest('label');if(keyLabel)keyLabel.hidden=true;
        const render=()=>{
          list.replaceChildren();
          const table=document.createElement('table');table.className='connect-numbers-table';
          table.innerHTML=`<thead><tr>${[t.name,t.number,t.pipeline,t.status,''].map(text=>`<th scope="col">${esc(text)}</th>`).join('')}</tr></thead><tbody></tbody>`;list.append(table);
          const body=table.querySelector('tbody');
          for(const channel of channels.filter(c=>adding||c.active||c.linked)){
            const tr=document.createElement('tr');body.append(tr);
            const cell=value=>{const td=document.createElement('td');td.textContent=value;tr.append(td);return td;};
            const name=cell(channel.name||channel.id);cell(channel.phone?String(channel.phone).replace(/^\+?/, '+'):'—');cell(channel.pipeline_name||'—');
            const status=cell(''),badge=document.createElement('span');badge.className='connect-number-status';badge.dataset.state=channel.phoneConflict?'warning':['connected','authenticated'].includes(String(channel.status).toLowerCase())?'connected':'other';badge.textContent=channel.phoneConflict?t.conflict:(channel.status||t.unknown);status.append(badge);
            const controls=cell('');controls.className='connect-number-actions';
            const actionButton=(label,action,scope)=>{if(!actions[scope])return;const b=document.createElement('button');b.type='button';b.className='outline';b.textContent=label;b.onclick=async()=>{b.disabled=true;try{if(current!==epoch)return;const result=await post({action,channel_id:channel.id},scope);if(!result)return;if(action==='channel_status'){channel.status=result.status||channel.status;channel.phone=result.phone||channel.phone;render();}else if(actions.view){const feedback=[...message.childNodes];await loadChannels(false);if(current===epoch&&!message.textContent)message.replaceChildren(...feedback);}}catch(e){if(current===epoch)message.textContent=e.message;}finally{if(current===epoch)b.disabled=false;}};controls.append(b);};
            if(channel.active||channel.linked){actionButton('Status','channel_status','view');if(!channel.phoneConflict)actionButton('Yenidən qoş','reconnect_channel','update');if(channel.active)actionButton('Ayır','disconnect_channel','delete');}
            else if(adding&&write&&!channel.phoneConflict){const choice=document.createElement('input');choice.type='checkbox';choice.value=channel.id;choice.setAttribute('aria-label',channel.name||channel.id);name.prepend(choice);}
            if(profile.member?.role==='owner'&&channel.active&&!channel.phoneConflict){const calls=document.createElement('button');calls.type='button';calls.className='outline';calls.textContent='Zənglər';calls.dataset.connectCalls=channel.id;controls.append(calls);}
          }
          if(!body.children.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=5;td.textContent=t.empty;tr.append(td);body.append(tr);}
        };
        const retry=button(t.retry,()=>loadChannels(false));retry.hidden=true;
        const loadChannels=async(useKey=false)=>{
          if(loading||current!==epoch)return;loading=true;const serial=++readSerial;retry.hidden=true;message.textContent=t.loading;list.setAttribute('aria-busy','true');
          try{
            const key=useKey?(panel.querySelector('[data-key]')?.value||''):'';
            const payload=command(profile,provider,{action:'channels',api_key:key},key?(connected(profile,provider)?'update':'create'):'view');
            const result=await request('/api/platform/integrations/connect',payload);
            if(current!==epoch||serial!==readSerial)return;channels=result.channels||[];message.textContent='';render();
          }catch(e){if(current===epoch&&serial===readSerial){message.textContent=e.message;retry.hidden=!actions.view;}}
          finally{if(current===epoch&&serial===readSerial){loading=false;list.removeAttribute('aria-busy');}}
        };
        if(write){
          const add=button(t.add,()=>{adding=!adding;add.setAttribute('aria-expanded',String(adding));if(keyLabel)keyLabel.hidden=!adding;available.hidden=!adding;save.hidden=!adding;render();});add.className='connect-number-add';add.setAttribute('aria-expanded','false');
          const available=button(t.available,()=>loadChannels(true));available.hidden=true;
          const save=button(t.save,async()=>{const key=panel.querySelector('[data-key]');const channel_ids=[...list.querySelectorAll('input:checked')].map(input=>input.value);if(!channel_ids.length)throw new Error('Kanal seçin.');const result=await post({api_key:key.value,channel_ids},connected(profile,provider)?'update':'create');if(!result)return;key.value='';if(actions.view)await loadChannels(false);});save.hidden=true;
        }
        render();if(actions.view)void loadChannels(false);
      }
    }
    window.tenantViewDemand.watch({provider,editable:'#'+id,resetOnHide:true,activate:install,reset:()=>{epoch++;profile=null;$(id)?.remove();}});
  }
})();
