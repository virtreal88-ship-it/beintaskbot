/* Private Operator controls mounted inside the existing Integrations section. */
(() => {
  const $ = (root, selector) => root.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let revision = 0, profile = null;
  const hostId = 'operatorIntegrationSettings';
  const operatorLinkAllowed = () => profile?.member?.role === 'owner' || (profile?.member?.permissions || []).some(value => value === 'operator_data' || value === 'operator_data:link');
  const text = {
    title: 'BeinSystems Operator',
    privacy: 'Только workspace BeinSystems. Токен хранится на сервере и не показывается повторно. Обновление подключения потребует повторно подтвердить связи.',
    token: 'SSO JWT Operator', save: 'Сохранить токен', test: 'Проверить подключение',
    disconnect: 'Отключить', search: 'Поиск аккаунта Operator',
    searchButton: 'Найти (от 4 символов)', entityType: 'Тип локальной записи',
    entityId: 'UUID CRM-компании или кейса', account: 'Идентификатор account из Operator',
    evidence: 'Основание ручной проверки', confirm: 'Подтвердить связь',
    linked: 'Связь подтверждена', waiting: 'Проверка…',
    userWarning: 'Связь с ERP-пользователем подтверждается вручную по паре account + login.',
    authPending: 'Обмен логина и пароля на SSO JWT пока не включён: API-метод авторизации не документирован. Эти поля не отправляются и не сохраняются.',
    contract: 'Создание тикетов и публичные ссылки включатся после подтверждения контрактов Operator.',
  };
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
    if (!response.ok || value.success === false) throw new Error(value.error || 'Операция не выполнена.');
    return value;
  }
  async function install(data) {
    const current = ++revision;
    profile = data;
    if (!data?.member || !Array.isArray(data.integrations) ||
        !data.integrations.some(row => row.provider === 'beinsystems_operator')) return;
    const host = document.getElementById('integrationSettingsHost');
    if (!host) return;
    host.querySelector('#'+hostId)?.remove();
    const panel = document.createElement('article');
    panel.id = hostId; panel.className = 'panel integration-modal-panel'; host.append(panel);
    let state;
    try { state = (await request('/api/platform/integrations/beinsystems-operator')).integration; }
    catch (error) { panel.textContent = error.message; return; }
    if (current !== revision) return;
    panel.innerHTML = '<h2>'+text.title+'</h2><p>'+text.privacy+'</p><p data-status></p><p data-message role="status"></p><section data-connection></section><hr><h3>'+text.search+'</h3><label>'+text.search+'<input data-search minlength="4" maxlength="120" autocomplete="off"></label><button type="button" data-search-button>'+text.searchButton+'</button><div data-results></div><button type="button" data-account-info>Показать разрешённые сведения об аккаунте</button><button type="button" data-profile-users>Показать пользователей аккаунта</button><div data-account-data></div><p>'+text.userWarning+'</p><hr><section data-link-controls><h3>Ручная связь с ERP-аккаунтом или пользователем</h3><label>'+text.entityType+'<select data-entity><option value="company">CRM company</option><option value="support_ticket">Support case</option><option value="contact">CRM contact → ERP user</option></select></label><label>'+text.entityId+'<input data-local autocomplete="off"></label><label>'+text.account+'<input data-account autocomplete="off"></label><label>Логин ERP-пользователя (для CRM-контакта)<input data-login autocomplete="off"></label><label>'+text.evidence+'<textarea data-evidence maxlength="500"></textarea></label><button type="button" data-confirm>'+text.confirm+'</button></section><p>'+text.contract+'</p>';
    const status = $(panel, '[data-status]'), message = $(panel, '[data-message]'), connection = $(panel, '[data-connection]');
    if (!operatorLinkAllowed()) $(panel, '[data-link-controls]').hidden = true;
    status.textContent = 'Состояние подключения: '+state.status+(state.api_ready ? '' : ' · API host не задан в серверной конфигурации');
    if (data.member.role === 'owner') {
      connection.innerHTML = '<label>Логин оператора<input type="text" data-operator-login autocomplete="off"></label><label>Пароль оператора<input type="password" data-operator-password autocomplete="off"></label><p>'+text.authPending+'</p><label>'+text.token+'<input type="password" data-token autocomplete="new-password"></label><button type="button" data-save>'+text.save+'</button><button type="button" data-test>'+text.test+'</button><button type="button" data-disconnect>'+text.disconnect+'</button>';
      const run = async (button, fn) => {
        button.disabled = true; message.textContent = text.waiting;
        try { const result = await fn(); if (current !== revision) return;
          if (result?.integration) { state = result.integration; status.textContent = 'Состояние подключения: '+state.status+(state.api_ready ? '' : ' · API host не задан в серверной конфигурации'); }
          message.textContent = result?.message || 'Готово.';
        } catch (error) { if (current === revision) message.textContent = error.message; }
        finally { button.disabled = false; }
      };
      $(panel,'[data-save]').onclick = event => run(event.currentTarget, async () => { if (state.configured && !window.confirm('Обновление подключения сделает старые привязки Operator устаревшими. Продолжить?')) return {message:'Отменено.'}; return request('/api/platform/integrations/beinsystems-operator', {action:'connect',token:$(panel,'[data-token]').value}); });
      $(panel,'[data-test]').onclick = event => run(event.currentTarget, () => request('/api/platform/integrations/beinsystems-operator', {action:'test'}));
      $(panel,'[data-disconnect]').onclick = event => run(event.currentTarget, async () => {
        if (!window.confirm('Отключить Operator? Подтверждённые ссылки станут устаревшими.')) return {message:'Отменено.'};
        return request('/api/platform/integrations/beinsystems-operator', {action:'disconnect'});
      });
    } else connection.textContent = 'Настройка подключения доступна владельцу workspace.';
    $(panel,'[data-search-button]').onclick = async event => {
      const button=event.currentTarget, value=$(panel,'[data-search]').value.trim(), results=$(panel,'[data-results]');
      button.disabled=true;message.textContent=text.waiting;
      try {
        const found=await request('/api/platform/operator/accounts',{search:value});
        if(current!==revision)return;
        results.replaceChildren();
        for(const row of found.accounts||[]){
          const item=document.createElement('button');item.type='button';item.textContent=row.account;
          item.onclick=()=>{$(panel,'[data-account]').value=row.account;};results.append(item);
        }
        message.textContent=(found.accounts||[]).length?'':'Аккаунты не найдены.';
      } catch(error) { if(current===revision)message.textContent=error.message; }
      finally { button.disabled=false; }
    };
    $(panel,'[data-account-info]').onclick = async event => {
      const button=event.currentTarget, target=$(panel,'[data-account-data]');
      button.disabled=true;message.textContent=text.waiting;
      try {
        const result=await request('/api/platform/operator/account-info',{
          entity_type:$(panel,'[data-entity]').value,entity_id:$(panel,'[data-local]').value.trim(),
          account:$(panel,'[data-account]').value.trim(),
        });
        if(current!==revision)return;
        target.textContent='Account: '+result.account_info.account+
          (result.account_info.last_contact?' · Последний контакт: '+result.account_info.last_contact:'')+
          (result.account_info.partner_name?' · Partner: '+result.account_info.partner_name:'');
        message.textContent='';
      } catch(error) { if(current===revision)message.textContent=error.message; }
      finally { button.disabled=false; }
    };
    $(panel,'[data-profile-users]').onclick = async event => {
      const button=event.currentTarget, target=$(panel,'[data-results]');
      button.disabled=true;message.textContent=text.waiting;
      try {
        const result=await request('/api/platform/operator/profile-users',{
          entity_type:$(panel,'[data-entity]').value,entity_id:$(panel,'[data-local]').value.trim(),
          account:$(panel,'[data-account]').value.trim(),
        });
        if(current!==revision)return;
        target.replaceChildren();
        for(const user of result.users||[]){
          const item=document.createElement('button');item.type='button';item.textContent=user.login;
          item.disabled=user.bindable!==true;
          item.onclick=()=>{$(panel,'[data-login]').value=user.login;};
          target.append(item);
        }
        message.textContent=(result.users||[]).length?'':'Пользователи не найдены.';
      } catch(error) { if(current===revision)message.textContent=error.message; }
      finally { button.disabled=false; }
    };
    $(panel,'[data-confirm]').onclick = async event => {
      const button=event.currentTarget;button.disabled=true;message.textContent=text.waiting;
      try {
        const entityType=$(panel,'[data-entity]').value;
        const payload={
          entity_type:entityType,entity_id:$(panel,'[data-local]').value.trim(),
          account:$(panel,'[data-account]').value.trim(),evidence:$(panel,'[data-evidence]').value.trim(),
        };
        if(entityType==='contact')payload.login=$(panel,'[data-login]').value.trim();
        const result=await request('/api/platform/operator/links',payload);
        if(current===revision)message.textContent=text.linked+': '+result.link.account;
      } catch(error) { if(current===revision)message.textContent=error.message; }
      finally { button.disabled=false; }
    };
  }
  window.tenantViewDemand.watch({
    provider:'beinsystems_operator',editable:'#'+hostId,resetOnHide:true,activate:install,
    reset:()=>{revision++;profile=null;document.getElementById(hostId)?.remove();},
  });
})();
