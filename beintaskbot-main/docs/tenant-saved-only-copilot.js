/* One approved owner action. No automatic dispatch, media load, or customer send. */
(()=>{
  const APPROVED_SCOPE_DIGEST='23ecbd195403ebb1098b1d3e66542cca3892bad8ff95a7a327d5d63f7f1df37a'; // Must match the server's reviewed target digest.
  const CASE='owner-saved-draft-v1';
  const permissionSnapshot=data=>JSON.stringify([data?.role_permissions,data?.capabilities?.modules,data?.capabilities?.chat_ai]);
  let generation=0,mountedKey='',mountedBox=null,profilePolicy=permissionSnapshot(window.tenantProfileSnapshot);
  const operator=()=>window.tenantOperator;
  const key=()=>{const member=operator()?.member,route=operator()?.currentDialog;return JSON.stringify([member?.tenant_id,member?.telegram_id,member?.role,member?.active,member?.permissions,profilePolicy,route?.provider,route?.channel_id,route?.conversation_id,route?.lead_id,route?.agent_id,route?.agent_version,route?.assignment_revision,route?.last_message_id]);};
  async function bind(){
    const current=key(),existing=document.getElementById('ownerSavedOnlyCopilot');
    if(mountedBox&&mountedKey===current){const panel=document.getElementById('operatorCopilot');if(panel&&!mountedBox.isConnected)panel.append(mountedBox);return;}
    const revision=++generation,member=operator()?.member,route=operator()?.currentDialog;
    existing?.remove();mountedBox?.remove();mountedBox=null;mountedKey='';
    if(!APPROVED_SCOPE_DIGEST||member?.role!=='owner'||member.active===false||!route)return;
    const scope=JSON.stringify([CASE,String(member.tenant_id),String(route.channel_id),String(route.conversation_id),Number(route.lead_id)]);
    const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(scope)))].map(n=>n.toString(16).padStart(2,'0')).join('');
    const panel=document.getElementById('operatorCopilot');if(digest!==APPROVED_SCOPE_DIGEST||revision!==generation||!panel)return;
    const captured=key(),box=document.createElement('section'),button=document.createElement('button'),status=document.createElement('p'),output=document.createElement('pre'),details=document.createElement('details'),summary=document.createElement('summary'),audit=document.createElement('pre');
    box.id='ownerSavedOnlyCopilot';mountedKey=captured;mountedBox=box;button.type='button';button.className='outline';button.textContent='Черновик по сохранённым данным: проверить стоимость';status.setAttribute('role','status');output.style.whiteSpace='pre-wrap';summary.textContent='Данные проверенного контекста';audit.style.whiteSpace='pre-wrap';details.append(summary,audit);box.append(button,status,output,details);panel.append(box);
    let quote=null,busy=false;
    const same=()=>revision===generation&&captured===key()&&box.isConnected;
    const body=()=>({saved_only:true,provider:'connect',lead_id:route.lead_id,channel_id:route.channel_id,conversation_id:route.conversation_id,expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id});
    async function api(method,data){
      const response=await fetch('/api/platform/ai/dialogs/copilot'+(method==='GET'?'?'+new URLSearchParams({...body(),saved_only:'1'}):''),{method,credentials:'same-origin',cache:'no-store',headers:{'X-Tenant-Id':String(member.tenant_id),'X-User-Id':String(member.telegram_id),...(method==='POST'?{'Content-Type':'application/json'}:{})},...(method==='POST'?{body:JSON.stringify({...body(),...data})}:{})});
      const payload=await response.json();if(!response.ok||!payload.success)throw Error(payload.error||'Запрос недоступен.');return payload.result;
    }
    button.onclick=async()=>{
      if(busy||!same())return;
      if(quote&&!window.confirm('Создать один черновик только по сохранённым данным? Максимальный внутренний резерв $'+(quote.reserve_microusd/1000000).toFixed(6)+'. Файлы не загружаются, сообщения клиенту не отправляются.'))return;
      busy=true;button.disabled=true;status.textContent='Проверяем сохранённый контекст…';
      try{
        const result=await api(quote?'POST':'GET',quote?{request_id:quote.request_id,cost_token:quote.cost_token}:undefined);if(!same())return;
        audit.textContent=JSON.stringify({snapshot:result.audit||result.result?.audit,usage:result.usage||result.result?.usage||null},null,2);
        if(result.text||result.result?.text){output.textContent=result.text||result.result.text;status.textContent='Черновик сохранён. Клиенту ничего не отправлено.';button.textContent='Проверить сохранённый результат';quote=null;}
        else if(result.state==='ready'&&result.cost_token){quote=result;status.textContent='Только сохранённые данные. Без ASR/анализа файлов и отправки. Максимальный внутренний резерв $'+(result.reserve_microusd/1000000).toFixed(6)+'; это не счёт провайдера.';button.textContent='Создать один черновик';}
        else{quote=null;status.textContent='Этот единственный запрос уже принят либо его результат неизвестен. Повторный запуск заблокирован.';button.textContent='Проверить состояние';}
      }catch(error){if(same()){quote=null;status.textContent=error.message||'Результат неизвестен. Проверьте состояние без повторного запуска.';button.textContent='Проверить состояние';}}
      finally{busy=false;if(same())button.disabled=false;}
    };
  }
  document.addEventListener('tenant-profile',event=>{profilePolicy=permissionSnapshot(event.detail);void bind();});
  document.addEventListener('tenant-chat-attachments-ready',()=>{void bind();});
  document.addEventListener('tenant-chat-close',()=>{generation++;mountedKey='';mountedBox?.remove();mountedBox=null;document.getElementById('ownerSavedOnlyCopilot')?.remove();});
  void bind();
})();
