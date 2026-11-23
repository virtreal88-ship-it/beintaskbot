/* Private Operator connection and employee mapping in Integrations. */
(() => {
  const $ = (root, selector) => root.querySelector(selector);
  const hostId = 'operatorIntegrationSettings';
  const statusHostId = 'operatorConnectionStatusHost';
  const endpoint = '/api/platform/integrations/beinsystems-operator';
  const waiting = 'Проверка…';
  let revision = 0, profile = null;
  async function request(path, payload) {
    const body = payload ? {
      ...payload, expected_tenant_id: profile?.member?.tenant_id,
      expected_user_id: profile?.member?.telegram_id,
    } : null;
    const response = await fetch(path, {
      credentials: 'same-origin', cache: 'no-store',
      ...(body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {}),
    });
    const value = await response.json();
    if (!response.ok || value.success === false) throw Object.assign(
      new Error(value.error || 'Операция не выполнена.'), {status:response.status});
    return value;
  }
  async function install(data) {
    const current = ++revision;
    profile = data;
    const host = document.getElementById('integrationSettingsHost');
    if (!host) return;
    host.querySelector('#'+hostId)?.remove();
    if (!data?.member || !data.integrations?.some(row => row.provider === 'beinsystems_operator')) return;
    const panel = document.createElement('article');
    panel.id = hostId; panel.className = 'panel integration-modal-panel';
    panel.innerHTML = '<h2>BeinSystems Operator</h2><p data-status role="status" aria-live="polite"></p>';
    host.append(panel);
    const valid = () => current === revision && panel.isConnected;
    const status = $(panel, '[data-status]');
    const statusHost=document.getElementById(statusHostId);
    if(statusHost){
      $(panel,'h2').remove();
      status.className='operator-connection-badge';
      statusHost.replaceChildren(status);
    }
    status.textContent=waiting;
    let state;
    const owner=data.member.role==='owner';
    const statusPath=owner?endpoint+'?'+new URLSearchParams({
      include_connected_login:'1',expected_tenant_id:String(data.member.tenant_id),
      expected_user_id:String(data.member.telegram_id),
    }):endpoint;
    try { state=(await request(statusPath)).integration; }
    catch(error) { if(valid())status.textContent=error.message; return; }
    if (!valid()) return;
    const isConnected=()=>state?.connected===true&&state?.status==='connected';
    const isConfigured=()=>state?.configured===true||state?.status==='configured';
    let verifiedLogin=owner&&isConnected()&&typeof state.connected_login==='string'?state.connected_login.trim():'';
    const renderStatus=()=>{
      const accountsReady=state?.accounts_only_ready===true;
      status.dataset.connected=String(isConnected()||accountsReady);
      status.textContent=isConnected()||accountsReady?'Подключено':'Не подключено';
      const loginDisplay=$(panel,'[data-connected-login]');
      if(loginDisplay){
        const login=isConnected()?verifiedLogin:'';
        loginDisplay.textContent=login;
        loginDisplay.hidden=!login;
      }
    };
    renderStatus();
    if(!owner)return;
    const connection=document.createElement('section');
    connection.dataset.connection='';
    connection.innerHTML='<p class="sub" data-connected-login hidden></p><form data-operator-auth-form autocomplete="off"><label>Логин оператора<input type="text" name="operator_login" data-operator-login placeholder="user@account" autocomplete="off" autocapitalize="none" spellcheck="false" enterkeyhint="next" required maxlength="240"></label><label>Пароль оператора<input type="password" name="operator_password" data-operator-password autocomplete="new-password" autocapitalize="none" enterkeyhint="go" required maxlength="512"></label><button type="button" data-show-password aria-label="Показать пароль" aria-pressed="false" style="display:inline-flex;align-items:center;justify-content:center;min-width:36px;min-height:36px"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" focusable="false" aria-hidden="true"><path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/><path data-eye-slash d="M3 3l18 18" opacity="0"/></svg></button><button type="submit" data-save>Подключить</button><p class="sub" data-auth-message role="status" aria-live="polite" aria-atomic="true" tabindex="-1"></p></form><div data-connection-next hidden><button type="button" data-finish-connection>Завершить подключение</button><button type="button" data-accounts-only>Подключить только аккаунты</button><div data-identity-confirmation></div></div>';
    panel.append(connection);
    renderStatus();
    const authForm=$(connection,'[data-operator-auth-form]');
    const authMessage=$(connection,'[data-auth-message]');
    const passwordInput=$(authForm,'[data-operator-password]'), showPassword=$(authForm,'[data-show-password]');
    const revealPassword=show=>{
      passwordInput.type=show?'text':'password';
      showPassword.setAttribute('aria-pressed',String(show));
      showPassword.setAttribute('aria-label',show?'Скрыть пароль':'Показать пароль');
      $(showPassword,'[data-eye-slash]').setAttribute('opacity',show?'1':'0');
    };
    showPassword.onclick=()=>{if(valid()&&!connectionBusy)revealPassword(passwordInput.type==='password');};
    const save=$(connection,'[data-save]'), finish=$(connection,'[data-finish-connection]');
    const limited=$(connection,'[data-accounts-only]'), next=$(connection,'[data-connection-next]');
    const identityHost=$(connection,'[data-identity-confirmation]');
    let connectionBusy=false;
    const identityPending=()=>Boolean(state?.pending_auth_received_at);
    const renderConnection=()=>{
      renderStatus();
      next.hidden=!(identityPending()&&!isConnected()&&!isConfigured());
      limited.hidden=state?.accounts_only_ready===true||identityHost.hasChildNodes();
      save.disabled=finish.disabled=limited.disabled=showPassword.disabled=connectionBusy;
    };
    const showAuth=(message,focus=false)=>{
      authMessage.textContent=message;
      if(focus){authMessage.focus({preventScroll:true});authMessage.scrollIntoView?.({block:'nearest'});}
    };
    const runConnection=async operation=>{
      if(!valid()||connectionBusy)return;
      connectionBusy=true;renderConnection();showAuth('');
      try{await operation();}
      catch(error){if(valid())showAuth(error.message,true);}
      finally{connectionBusy=false;if(valid())renderConnection();}
    };
    renderConnection();
    authForm.addEventListener('submit',event=>{
      event.preventDefault();
      if(!valid()||connectionBusy)return;
      const login=$(authForm,'[data-operator-login]').value.trim();
      let password=passwordInput.value;passwordInput.value='';revealPassword(false);
      if(!login||!password){password='';showAuth('Введите логин и пароль оператора.',true);return;}
      identityHost.replaceChildren();
      void runConnection(async()=>{
        const pending=request(endpoint,{action:'login',login,password});
        password='';
        const result=await pending;
        if(!valid())return;
        if(result.integration)state=result.integration;
        if(state?.pending_auth_received!==true)throw Error('Сервер не подтвердил вход в Operator.');
        if(isConnected()&&verifiedLogin){
          const refreshed=await request(endpoint,{action:'renew_connection',candidate_received_at:state.pending_auth_received_at});
          if(!valid())return;
          state=refreshed.integration;
          showAuth('Авторизация Operator обновлена.');
        }
        renderStatus();
      });
    });
    finish.onclick=()=>{
      if(!valid()||next.hidden||connectionBusy||
          !window.confirm('Подтвердить доступ Operator? При подключении account ID сохранятся в каталоге. Компании CRM созданы не будут.'))return;
      identityHost.replaceChildren();
      return runConnection(async()=>{
        const result=await request('/api/platform/operator/accounts',{action:'import'});
        if(!valid())return;
        if(result.integration)state=result.integration;
        if(!isConnected())throw Error('Operator не подтвердил подключение.');
        verifiedLogin='';
        renderStatus();
        void loadUsers();
        const refreshed=await request(statusPath);
        if(!valid())return;
        if(refreshed.integration)state=refreshed.integration;
        verifiedLogin=isConnected()&&typeof state.connected_login==='string'?state.connected_login.trim():'';
        renderStatus();
      });
    };
    limited.onclick=()=>{
      if(next.hidden||limited.hidden||connectionBusy)return;
      return runConnection(async()=>{
        const candidate=state.pending_auth_received_at;
        const result=await request(endpoint,{action:'prepare_accounts_capability',candidate_received_at:candidate});
        if(!valid())return;
        const check=result.accounts_only_confirmation;
        if(typeof check?.identifier!=='string'||!check.identifier||check.candidate_received_at!==candidate)
          throw Error('Не удалось подтвердить учётную запись Operator.');
        const identity=document.createElement('p');
        identity.textContent=check.identifier;
        const confirm=document.createElement('button'), cancel=document.createElement('button');
        confirm.type=cancel.type='button';confirm.textContent='Подтвердить';cancel.textContent='Отмена';
        cancel.onclick=()=>{identityHost.replaceChildren();showAuth('');renderConnection();limited.focus();};
        confirm.onclick=()=>{
          if(!valid()||!confirm.isConnected||connectionBusy||state.pending_auth_received_at!==candidate)return;
          confirm.disabled=true;
          return runConnection(async()=>{
            try {
              const saved=await request(endpoint,{action:'confirm_accounts_capability',
                acknowledged_identifier:check.identifier,candidate_received_at:candidate});
              if(!valid())return;
              if(saved.integration)state=saved.integration;
              if(state?.accounts_only_ready!==true)throw Error('Operator не подтвердил доступ к аккаунтам.');
              identityHost.replaceChildren();renderStatus();
            }finally{if(valid())confirm.disabled=false;}
          });
        };
        identityHost.replaceChildren(identity,confirm,cancel);
        confirm.focus();
      });
    };

    const employees=document.createElement('section');
    employees.dataset.employeeControls='';
    employees.innerHTML='<hr><h3>Сотрудники</h3><form data-employee-form><label>Сотрудник Operator<select data-employee-user required><option value=""></option></select></label><label>Сотрудник CRM<select data-employee-member required><option value=""></option></select></label><button type="submit" data-confirm-employee>Привязать</button><p class="sub" data-employee-message role="status" aria-live="polite" aria-atomic="true" tabindex="-1"></p></form><div data-employee-links></div>';
    panel.append(employees);
    const form=$(employees,'[data-employee-form]'), member=$(form,'[data-employee-member]');
    const user=$(form,'[data-employee-user]'), confirm=$(form,'[data-confirm-employee]');
    const message=$(form,'[data-employee-message]'), linksHost=$(employees,'[data-employee-links]');
    let members=[], selectionRevision='', lookupBusy=false, mappingBusy=false;
    confirm.disabled=true;
    const showEmployee=(value,focus=false)=>{
      if(!valid())return;
      message.textContent=value;
      if(focus){message.focus({preventScroll:true});message.scrollIntoView?.({block:'nearest'});}
    };
    const staffName=row=>row?.display_name||[row?.first_name,row?.last_name].filter(Boolean).join(' ');
    function renderEmployeeLinks(links){
      linksHost.replaceChildren();
      for(const link of links||[]){
        if(link.status==='revoked')continue;
        const staff=members.find(row=>Number(row.telegram_id)===Number(link.member_telegram_id));
        const item=document.createElement('p');
        item.textContent=link.login+' ↔ '+(staffName(staff)||String(link.member_telegram_id));
        linksHost.append(item);
      }
    }
    let links=[];
    const renderBind=()=>{
      confirm.disabled=mappingBusy||!selectionRevision||!user.value||user.selectedOptions[0]?.disabled||
        !members.some(row=>Number(row.telegram_id)===Number(member.value));
    };
    request('/api/platform/members').then(result=>{
      if(!valid())return;
      members=(result.members||[]).filter(row=>row.active!==false);
      for(const row of members)member.append(new Option(staffName(row)||String(row.telegram_id),String(row.telegram_id)));
      renderEmployeeLinks(links);
      renderBind();
    }).catch(error=>showEmployee(error.message));
    request('/api/platform/operator/employee-links').then(result=>{
      if(valid()){links=result.links||[];renderEmployeeLinks(links);}
    }).catch(error=>{if(!message.textContent)showEmployee(error.message);});
    member.addEventListener('change',renderBind);
    user.addEventListener('change',renderBind);
    async function loadUsers(interactive=false,keepError=false){
      if(!valid()||selectionRevision||lookupBusy||mappingBusy)return;
      user.disabled=member.disabled=false;
      lookupBusy=true;
      user.setAttribute('aria-busy','true');
      if(!keepError)showEmployee('');
      try{
        const result=await request('/api/platform/operator/employee-users',{});
        if(!valid())return;
        if(typeof result.selection_revision!=='string'||!result.selection_revision)
          throw Error('Не удалось подтвердить список сотрудников Operator.');
        user.replaceChildren(new Option('',''));
        for(const row of result.users||[]){
          const option=new Option(row.login,row.login);
          option.disabled=row.bindable===false;
          user.append(option);
        }
        selectionRevision=result.selection_revision;
        renderBind();
      }catch(error){showEmployee(error.message,interactive);}
      finally{
        lookupBusy=false;
        if(valid())user.removeAttribute('aria-busy');
      }
    }
    user.addEventListener('focus',()=>{void loadUsers(true);});
    user.addEventListener('pointerdown',()=>{void loadUsers(true);});
    if(isConnected())void loadUsers();
    form.addEventListener('submit',async event=>{
      event.preventDefault();
      if(!valid()||mappingBusy)return;
      const selected=Number(member.value), login=user.value;
      if(!members.some(row=>Number(row.telegram_id)===selected)||!selectionRevision||!login||user.selectedOptions[0]?.disabled)return;
      mappingBusy=true;confirm.disabled=user.disabled=member.disabled=true;showEmployee('');
      try{
        await request('/api/platform/operator/employee-links',{member_telegram_id:selected,login,selection_revision:selectionRevision});
        if(!valid())return;
        const result=await request('/api/platform/operator/employee-links');
        if(valid()){links=result.links||[];renderEmployeeLinks(links);user.value=member.value='';}
      }catch(error){
        showEmployee(error.message,true);
        if(error.status===409&&valid()){
          selectionRevision='';
          user.replaceChildren(new Option('',''));
        }
      }finally{
        mappingBusy=false;
        if(!valid())return;
        user.disabled=member.disabled=false;
        renderBind();
        if(!selectionRevision)void loadUsers(false,true);
      }
    });
  }
  window.tenantViewDemand.watch({
    provider:'beinsystems_operator',editable:'#'+hostId,resetOnHide:true,activate:install,
    reset:()=>{revision++;profile=null;document.getElementById(hostId)?.remove();document.getElementById(statusHostId)?.replaceChildren();},
  });
})();
