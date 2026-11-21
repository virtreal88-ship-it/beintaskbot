/* Private Operator controls mounted inside the existing Integrations section. */
(() => {
  const $ = (root, selector) => root.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let revision = 0, profile = null;
  const hostId = 'operatorIntegrationSettings';
  const operatorLinkAllowed = () => profile?.member?.role === 'owner' || (profile?.member?.permissions || []).some(value => value === 'operator_data' || value === 'operator_data:link');
  const text = {
    title: 'BeinSystems Operator',
    privacy: 'Только workspace BeinSystems. Текущий токен хранится на сервере и не показывается повторно. Новый JWT не изменит рабочее подключение до подтверждения доступа.',
    save: 'Получить токен Operator',
    tokenPending: 'JWT получен и зашифрован отдельно. Доступ не подтверждён; действующее подключение не менялось.',
    disconnect: 'Отключить', search: 'Поиск аккаунта Operator',
    searchButton: 'Найти (от 4 символов)', entityType: 'Тип локальной записи',
    entityId: 'UUID CRM-компании или кейса', account: 'Идентификатор account из Operator',
    evidence: 'Основание ручной проверки', confirm: 'Подтвердить связь',
    linked: 'Связь подтверждена', waiting: 'Проверка…',
    userWarning: 'Связь с ERP-пользователем подтверждается вручную по паре account + login.',
    authPending: 'Пароль отправляется по HTTPS только для получения JWT и не сохраняется. Проверка читает не больше одной записи из /user/accounts. Она не подтверждает способ входа; pending JWT не активируется и не меняет действующее подключение.',
    contract: 'Создание тикетов и публичные ссылки включатся после подтверждения контрактов Operator.',
    employeeLink: 'Пользователь Operator выбирается из ответа API и связывается с сотрудником Beintaskbot по паре account + login. Документация пока не описывает отдельный стабильный user ID. Сервисные вызовы остаются под ограниченным SSO-пользователем подключения; Beintaskbot отдельно фиксирует сотрудника, который подтвердил связь.',
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
  async function requestGet(path, params = {}) {
    const query = new URLSearchParams({
      ...params,
      expected_tenant_id: String(profile?.member?.tenant_id || ''),
      expected_user_id: String(profile?.member?.telegram_id || ''),
    });
    const response = await fetch(path + '?' + query.toString(), {
      credentials: 'same-origin', cache: 'no-store',
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
    panel.innerHTML = '<h2>'+text.title+'</h2><p>'+text.privacy+'</p><p data-status role="status" aria-live="polite" aria-atomic="true"></p><p data-message role="status" aria-live="polite" aria-atomic="true"></p><section data-connection></section><hr><h3>'+text.search+'</h3><label>'+text.search+'<input type="search" data-search minlength="4" maxlength="120" autocomplete="off"></label><button type="button" data-search-button>'+text.searchButton+'</button><div data-results></div><button type="button" data-account-info>Показать разрешённые сведения об аккаунте</button><button type="button" data-profile-users>Показать пользователей аккаунта</button><div data-account-data></div><p>'+text.userWarning+'</p><hr><section data-link-controls><h3>Ручная связь с ERP-аккаунтом или пользователем</h3><label>'+text.entityType+'<select data-entity><option value="company">CRM company</option><option value="support_ticket">Support case</option><option value="contact">CRM contact → ERP user</option></select></label><label>'+text.entityId+'<input data-local autocomplete="off"></label><label>'+text.account+'<input data-account autocomplete="off"></label><label>Логин ERP-пользователя (для CRM-контакта)<input data-login autocomplete="off"></label><label>'+text.evidence+'<textarea data-evidence maxlength="500"></textarea></label><button type="button" data-confirm>'+text.confirm+'</button></section><p>'+text.contract+'</p>';
    const employeeControls = document.createElement('section');
    employeeControls.dataset.employeeControls = '';
    employeeControls.innerHTML = '<hr><h3>Сотрудники Beintaskbot ↔ Operator</h3><p>'+text.employeeLink+'</p><label>Сотрудник Beintaskbot<select data-employee-member><option value="">Выберите сотрудника</option></select></label><label>Operator account<input data-employee-account maxlength="240" autocomplete="off"></label><button type="button" data-load-employee-users>Загрузить список пользователей Operator</button><label>Пользователь Operator (login)<select data-employee-user><option value="">Сначала загрузите список</option></select></label><label>'+text.evidence+'<textarea data-employee-evidence maxlength="500"></textarea></label><button type="button" data-confirm-employee>Подтвердить связь сотрудника</button><div data-employee-links></div>';
    panel.append(employeeControls);
    const status = $(panel, '[data-status]'), message = $(panel, '[data-message]'), connection = $(panel, '[data-connection]');
    if (!operatorLinkAllowed()) $(panel, '[data-link-controls]').hidden = true;
    if (data.member.role !== 'owner') employeeControls.hidden = true;
    const renderStatus = () => {
      const parts = ['Состояние подключения: '+state.status];
      if (!state.api_ready) parts.push('API host не задан в серверной конфигурации');
      if (state.accounts_only_ready) parts.push('Подключено для аккаунтов: подтверждение разрешает попытку, доступ Operator к account проверяется отдельно');
      if (state.pending_auth_received) {
        if (state.pending_auth_access_confirmed === true) parts.push('У pending-кандидата есть отметка предыдущей проверки /user/accounts; конкретный результат здесь не указан');
        else parts.push('JWT сохранён как pending; чтение account не подтверждено');
      }
      status.textContent = parts.join(' · ');
      const testButton=connection.querySelector('[data-test]');
      if(testButton)testButton.disabled=!(state.pending_auth_received||state.connected||state.configured);
      const createCompaniesButton=connection.querySelector('[data-create-companies]');
      if(createCompaniesButton)createCompaniesButton.disabled=!(state.connected||state.configured);
      const firstBatchButton=connection.querySelector('[data-first-company-batch]');
      if(firstBatchButton)firstBatchButton.hidden=!(state.first_company_batch_preview_enabled &&
        state.accounts_only_ready && !state.configured && !state.connected);
    };
    renderStatus();
    if (data.member.role === 'owner') {
      connection.innerHTML = '<form data-operator-auth-form autocomplete="off"><label>Логин оператора<input type="text" name="operator_login" data-operator-login autocomplete="off" autocapitalize="none" spellcheck="false" enterkeyhint="next" required maxlength="240"></label><label>Пароль оператора<input type="password" name="operator_password" data-operator-password autocomplete="new-password" autocapitalize="none" enterkeyhint="go" required maxlength="512"></label><p>'+text.authPending+'</p><button type="submit" data-save>'+text.save+'</button><p class="sub" data-auth-message role="status" aria-live="polite" aria-atomic="true" tabindex="-1"></p></form><div data-accounts-capability aria-live="polite"></div><button type="button" data-test>Проверить Operator</button><button type="button" data-import>Импортировать доступные accounts</button><button type="button" data-create-companies>Создать CRM-компании из accounts</button><button type="button" data-disconnect>'+text.disconnect+'</button><p>Проверка читает только /user/me. Импорт запрашивает список /user/accounts и сохраняет полученные account ID; полнота выдачи Operator не проверена. Пропавшие записи не удаляются, данные в Operator не отправляются.</p><p>Создание компаний — отдельное подтверждаемое действие по account ID, полученному из запроса /user/accounts текущего активного подключения. Название новой компании берётся из account; при совпадении названия автоматической привязки не будет — требуется ручная проверка. Телефон и email остаются пустыми. В Operator ничего не записывается.</p><div data-catalog aria-live="polite"></div>';
      const testControl = $(connection, '[data-test]');
      testControl.textContent = 'Проверить доступ к accounts';
      const testDescription = Array.from(connection.querySelectorAll('p'))
        .find(item => item.textContent.includes('/user/me'));
      if (testDescription) testDescription.textContent =
        'Проверка отправляет поиск с лимитом 1 в /user/accounts. Для pending JWT это проверка только чтения accounts, а не способа входа. Без результата чтение конкретного account не подтверждается; импорт и активация не выполняются.';
      testControl.disabled=!(state.pending_auth_received||state.connected||state.configured);
      const authForm = $(connection, '[data-operator-auth-form]');
      $(connection, '[data-operator-login]').placeholder = 'user@account';
      const authMessage = $(connection, '[data-auth-message]');
      const saveButton = $(connection, '[data-save]');
      const showAuthMessage = (value, reveal = false) => {
        authMessage.textContent = value;
        if (!reveal) return;
        authMessage.focus({preventScroll: true});
        authMessage.scrollIntoView?.({block: 'nearest'});
      };
      const capabilityHost=$(connection,'[data-accounts-capability]');
      const renderAccountsCapability=()=>{
        capabilityHost.replaceChildren();
        if(state.accounts_only_ready){
          const label=document.createElement('p');
          label.textContent='Подключено для аккаунтов. Подтверждение разрешает только попытку добавления; доступ Operator к выбранному account проверяется отдельно и может не подтвердиться. Остальные функции Operator не активированы.';
          capabilityHost.append(label);return;
        }
        if(!state.pending_auth_received||state.connected||state.configured)return;
        const prepare=document.createElement('button');prepare.type='button';
        prepare.textContent='Проверить личность для аккаунтного доступа';
        const note=document.createElement('p');
        note.textContent='Отдельное подтверждение владельца разрешит только попытку ручного добавления одного account в CRM на 24 часа. Доступ Operator к выбранному account отдельно проверяется при добавлении и может не подтвердиться. Общая связь Operator не активируется.';
        capabilityHost.append(note,prepare);
        prepare.onclick=async()=>{
          prepare.disabled=true;message.textContent=text.waiting;
          try{
            const preview=await request('/api/platform/integrations/beinsystems-operator',{
              action:'prepare_accounts_capability',candidate_received_at:state.pending_auth_received_at});
            if(current!==revision)return;
            const confirmation=preview.accounts_only_confirmation||{};
            const prompt=document.createElement('section');
            const identity=document.createElement('p');
            identity.textContent='Проверенный Operator identity: '+String(confirmation.identifier||'');
            const confirm=document.createElement('button');confirm.type='button';
            confirm.textContent='Подтверждаю эту учётную запись для добавления аккаунтов';
            const cancel=document.createElement('button');cancel.type='button';cancel.textContent='Отмена';
            cancel.onclick=()=>prompt.remove();
            confirm.onclick=async()=>{
              confirm.disabled=true;message.textContent=text.waiting;
              try{
                const saved=await request('/api/platform/integrations/beinsystems-operator',{
                  action:'confirm_accounts_capability',
                  acknowledged_identifier:confirmation.identifier,
                  candidate_received_at:confirmation.candidate_received_at,
                });
                if(current!==revision)return;
                if(saved.integration){state=saved.integration;renderStatus();renderAccountsCapability();}
                message.textContent='Доступ подключён только для аккаунтов на 24 часа.';
              }catch(error){if(current===revision)message.textContent=error.message;}
              finally{if(current===revision)confirm.disabled=false;}
            };
            prompt.append(identity,confirm,cancel);capabilityHost.replaceChildren(note,prompt);
            message.textContent='Проверьте учётную запись Operator перед подтверждением.';
          }catch(error){if(current===revision)message.textContent=error.message;}
          finally{if(current===revision)prepare.disabled=false;}
        };
      };
      renderAccountsCapability();
      if (state.pending_auth_received) showAuthMessage(text.tokenPending);
      const run = async (button, fn) => {
        button.disabled = true; message.textContent = text.waiting;
        try { const result = await fn(); if (current !== revision) return;
          if (result?.integration) { state = result.integration; renderStatus(); renderAccountsCapability(); }
          if (result?.verification?.request_accepted === true) {
            const accountRead = result.verification.account_read === true;
            const outcome = accountRead
              ? 'Operator принял запрос /user/accounts и вернул одну запись; чтение account подтверждено.'
              : 'Operator принял запрос /user/accounts, совпадений нет; чтение конкретного account не подтверждено.';
            const exactMatch = accountRead
              ? (result.verification.match_found === true
                ? ' Точное совпадение найдено.' : ' Точного совпадения нет.')
              : '';
            const sourceNote = result.verification.source === 'pending'
              ? ' Использован сохранённый pending JWT; это проверка только доступа к accounts, не подтверждение нового способа входа. JWT остаётся pending; действующее подключение не менялось.'
              : ' Проверено действующее подключение; pending JWT и способ нового входа не проверялись. Подключение не менялось.';
            message.textContent = outcome+exactMatch+sourceNote;
          } else if (Number.isInteger(result?.imported)) {
            message.textContent = 'Импортировано account ID: '+result.imported+
              (result.activated ? '. Доступ к /user/me и /user/accounts подтверждён; подключение активировано.'
                : '. Существующее подключение сохранено.');
            void loadCatalog();
          } else if (result?.company_import) {
            const counts=result.company_import;
            const collisionCount=Number.isFinite(Number(counts.name_collision_count))
              ? Math.max(0,Number(counts.name_collision_count)) : 0;
            const collisionAccounts=(Array.isArray(counts.name_collision_accounts)
              ? counts.name_collision_accounts : []).slice(0,20)
              .map(account=>String(account).slice(0,240)).filter(Boolean);
            const collisionDetails=collisionCount
              ? ' Совпадение названия, требуется ручная проверка ('+collisionCount+')'+
                (collisionAccounts.length ? ': '+collisionAccounts.join(', ') : '')+
                (counts.name_collision_truncated ? ' …' : '')+'.'
              : '';
            message.textContent=(counts.partial ? 'Первая выборка (не весь каталог): ' : 'Проверено аккаунтов: ')+counts.checked+
              ' · создано компаний: '+counts.created+
              (counts.partial ? ' · уже есть без изменений: ' : ' · повторно связано: ')+counts.reused+
              ' · ручная проверка: '+counts.manual_review+'.'+collisionDetails;
          } else {
            message.textContent = result?.integration?.pending_auth_received
              ? text.tokenPending : (result?.message || 'Готово.');
          }
        } catch (error) { if (current === revision) message.textContent = error.message; }
        finally { button.disabled = false; }
      };
      authForm.addEventListener('submit', async event => {
        event.preventDefault();
        const login = $(authForm, '[data-operator-login]').value.trim();
        const passwordInput = $(authForm, '[data-operator-password]');
        let password = passwordInput.value;
        passwordInput.value = '';
        if (!login || !password) {
          password = '';
          showAuthMessage('Введите логин и пароль оператора.', true);
          return;
        }
        saveButton.disabled = true;
        showAuthMessage(text.waiting);
        try {
          const pending = request('/api/platform/integrations/beinsystems-operator', {
            action:'login', login, password
          });
          password = '';
          const result = await pending;
          if (current !== revision) return;
          if (result?.integration) { state = result.integration; renderStatus(); renderAccountsCapability(); }
          if (result?.integration?.pending_auth_received === true) showAuthMessage(text.tokenPending, true);
          else showAuthMessage('Сервер не подтвердил получение JWT. Действующее подключение не изменено.', true);
        } catch (error) {
          if (current === revision) showAuthMessage(error.message, true);
        } finally {
          password = '';
          passwordInput.value = '';
          if (current === revision) saveButton.disabled = false;
        }
      });
      let catalogRevision=0;
      const loadCatalog = async (search='') => {
        const requestId=++catalogRevision, host=$(panel,'[data-catalog]');
        try {
          const result=await requestGet('/api/platform/operator/accounts',{search});
          if(current!==revision||requestId!==catalogRevision)return;
          host.replaceChildren();
          const heading=document.createElement('h4');heading.textContent='Импортированный каталог account ID';host.append(heading);
          const label=document.createElement('label');label.textContent='Найти account ID локально';
          const input=document.createElement('input');input.type='search';input.maxLength=120;input.autocomplete='off';
          input.value=search;input.dataset.catalogSearch='';label.append(input);host.append(label);
          const find=document.createElement('button');find.type='button';find.textContent='Искать в локальном каталоге';
          find.onclick=()=>void loadCatalog(input.value.trim());host.append(find);
          const results=document.createElement('div');results.dataset.catalogResults='';host.append(results);
          for(const row of result.accounts||[]){
            const item=document.createElement('button');item.type='button';item.textContent=row.account;
            item.onclick=()=>{$(panel,'[data-account]').value=row.account;};
            results.append(item);
          }
          if(!(result.accounts||[]).length){const empty=document.createElement('p');empty.textContent='Аккаунты в локальном каталоге не найдены.';results.append(empty);}
        } catch(error) { if(current===revision&&requestId===catalogRevision)host.textContent=error.message; }
      };
      void loadCatalog();
      $(panel,'[data-test]').onclick = event => run(event.currentTarget, async () => {
        const search = $(panel,'[data-search]').value.trim();
        if (search.length < 4) throw new Error('Введите не менее четырёх символов известного account.');
        return request('/api/platform/integrations/beinsystems-operator',{action:'test',search});
      });
      $(panel,'[data-import]').onclick = event => run(event.currentTarget, async () =>
        request('/api/platform/operator/accounts',{action:'import'}));
      $(panel,'[data-create-companies]').onclick = event => run(event.currentTarget, async () => {
        if(!(state.connected||state.configured))throw new Error('Сначала проверьте действующее подключение Operator.');
        if(!window.confirm('Создать недостающие CRM-компании по свежему списку активного Operator? Совпадения названия отправятся на ручную проверку.'))return {message:'Отменено.'};
        return request('/api/platform/operator/accounts',{action:'create_companies'});
      });
      const batchButton=document.createElement('button');
      batchButton.type='button'; batchButton.dataset.firstCompanyBatch='';
      batchButton.textContent='Предпросмотр первой выборки: до 100 аккаунтов';
      batchButton.hidden=true;
      const batchHost=document.createElement('div');batchHost.dataset.firstBatchPreview='';batchHost.setAttribute('aria-live','polite');
      connection.append(batchButton,batchHost);
      renderStatus();
      batchButton.onclick=event=>run(event.currentTarget,async()=>{
        if (batchButton.hidden) return {};
        batchHost.replaceChildren();
        const result=await request('/api/platform/operator/accounts',{action:'preview_first_company_batch'});
        if(current!==revision || batchButton.hidden) return {};
        const preview=result.company_batch_preview;
        const heading=document.createElement('p');
        heading.textContent='Первая выборка Operator: '+preview.checked+' из максимум 100. Это не полный каталог.';
        const rows=document.createElement('ul');
        const canConfirm=typeof preview.receipt==='string' && !!preview.receipt;
        const names={create:canConfirm?'Создать':'Кандидат на создание',reuse:'Уже есть, без изменений',manual_review:'Ручная проверка'};
        for(const row of preview.rows){
          const item=document.createElement('li');item.textContent=row.account+' · '+names[row.action];rows.append(item);
        }
        if (!canConfirm) {
          const note=document.createElement('p');
          note.textContent='Доступен только предпросмотр. CRM-компании не создаются.';
          batchHost.append(heading,rows,note);
          return {message: note.textContent};
        }
        const confirm=document.createElement('button');confirm.type='button';
        confirm.dataset.firstBatchConfirm='';
        confirm.textContent='Подтвердить создание новых компаний из этой выборки';
        confirm.disabled=!preview.rows.some(row=>row.action==='create');
        const cancel=document.createElement('button');cancel.type='button';cancel.textContent='Отменить';
        cancel.dataset.firstBatchCancel='';
        cancel.onclick=()=>batchHost.replaceChildren();
        confirm.onclick=event=>run(event.currentTarget,async()=>{
          if(current!==revision || !batchHost.contains(confirm) || batchButton.hidden) return {};
          batchHost.replaceChildren();
          return request('/api/platform/operator/accounts',{
            action:'confirm_first_company_batch',receipt:preview.receipt
          });
        });
        batchHost.append(heading,rows,confirm,cancel);
        return {message: preview.rows.length ? 'Проверьте выборку перед подтверждением.' :
          'Operator не вернул аккаунтов в этой выборке. Это не проверка полноты каталога.'};
      });
      $(panel,'[data-disconnect]').onclick = event => run(event.currentTarget, async () => {
        if (!window.confirm('Отключить Operator? Подтверждённые ссылки станут устаревшими.')) return {message:'Отменено.'};
        return request('/api/platform/integrations/beinsystems-operator', {action:'disconnect'});
      });
    } else connection.textContent = 'Настройка подключения доступна владельцу workspace.';
    const employeeMember = $(panel, '[data-employee-member]');
    const employeeUser = $(panel, '[data-employee-user]');
    const employeeLinksHost = $(panel, '[data-employee-links]');
    let workspaceMembers = [];
    const renderEmployeeLinks = links => {
      employeeLinksHost.replaceChildren();
      for (const link of links || []) {
        const staff = workspaceMembers.find(row => Number(row.telegram_id) === Number(link.member_telegram_id));
        const item = document.createElement('p');
        item.append(document.createTextNode((staff?.display_name || staff?.first_name || String(link.member_telegram_id)+'')+
          ' → '+link.account+'/'+link.login+' · '+link.status+' '));
        if (link.status !== 'revoked') {
          const revoke = document.createElement('button'); revoke.type = 'button'; revoke.textContent = 'Отозвать';
          revoke.onclick = async () => {
            revoke.disabled = true; message.textContent = text.waiting;
            try {
              await request('/api/platform/operator/employee-links/'+encodeURIComponent(link.id)+'/revoke', {});
              const result = await request('/api/platform/operator/employee-links');
              if (current === revision) { renderEmployeeLinks(result.links); message.textContent = 'Связь отозвана.'; }
            } catch (error) { if (current === revision) message.textContent = error.message; }
            finally { revoke.disabled = false; }
          };
          item.append(revoke);
        }
        employeeLinksHost.append(item);
      }
    };
    if (data.member.role === 'owner') {
      Promise.all([request('/api/platform/members'), request('/api/platform/operator/employee-links')]).then(([membersResult, linksResult]) => {
        if (current !== revision) return;
        workspaceMembers = (membersResult.members || []).filter(row => row.active !== false);
        for (const row of workspaceMembers) {
          const option = document.createElement('option');
          option.value = String(row.telegram_id);
          option.textContent = (row.display_name || [row.first_name, row.last_name].filter(Boolean).join(' ') || String(row.telegram_id))+' · '+row.role;
          employeeMember.append(option);
        }
        renderEmployeeLinks(linksResult.links);
      }).catch(error => { if (current === revision) message.textContent = error.message; });
      $(panel,'[data-load-employee-users]').onclick = async event => {
        const button=event.currentTarget, account=$(panel,'[data-employee-account]').value.trim();
        button.disabled=true;message.textContent=text.waiting;
        try {
          const result=await request('/api/platform/operator/employee-users',{account});
          if(current!==revision)return;
          employeeUser.replaceChildren();
          const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='Выберите пользователя';employeeUser.append(placeholder);
          for(const user of result.users||[]){const option=document.createElement('option');option.value=user.login;option.textContent=user.login;employeeUser.append(option);}
          message.textContent=(result.users||[]).length?'Пользователи Operator загружены.':'Пользователи Operator не найдены.';
        } catch(error) { if(current===revision)message.textContent=error.message; }
        finally { button.disabled=false; }
      };
      $(panel,'[data-confirm-employee]').onclick = async event => {
        const button=event.currentTarget;button.disabled=true;message.textContent=text.waiting;
        try {
          if (state.status !== 'connected') throw new Error('Сначала подключите Operator и проверьте подключение.');
          if (!/^[1-9][0-9]*$/.test(employeeMember.value)) throw new Error('Выберите сотрудника Beintaskbot.');
          if (!employeeUser.value) throw new Error('Выберите пользователя Operator из загруженного списка.');
          const result=await request('/api/platform/operator/employee-links',{
            member_telegram_id:Number(employeeMember.value),account:$(panel,'[data-employee-account]').value.trim(),
            login:employeeUser.value,evidence:$(panel,'[data-employee-evidence]').value.trim(),
          });
          const links=await request('/api/platform/operator/employee-links');
          if(current===revision){renderEmployeeLinks(links.links);message.textContent='Связь подтверждена: '+result.link.account+'/'+result.link.login;}
        } catch(error) { if(current===revision)message.textContent=error.message; }
        finally { button.disabled=false; }
      };
    }
    let directoryRevision=0;
    $(panel,'[data-search-button]').onclick = async event => {
      const button=event.currentTarget, rawValue=$(panel,'[data-search]').value,
        value=rawValue.trim(), results=$(panel,'[data-results]');
      const requestId=++directoryRevision;
      if(data.member.role==='owner'){
        results.replaceChildren();
        if(rawValue.length>120||value.length<4||value.length>120||/[\u0000-\u001f\u007f]/.test(rawValue)){
          results.textContent='Введите от 4 до 120 символов без управляющих знаков.';return;
        }
        button.disabled=true;message.textContent=text.waiting;
        try{
          const found=await request('/api/platform/integrations/beinsystems-operator',
            {action:'directory_search',search:value});
          if(current!==revision||requestId!==directoryRevision)return;
          const result=found.directory||{};
          if(result.account_id){
            if(result.source==='active'){
              const item=document.createElement('button');item.type='button';item.textContent=result.account_id;
              item.setAttribute('aria-label','Выбрать account ID '+result.account_id);
              item.onclick=()=>{$(panel,'[data-account]').value=result.account_id;};results.append(item);
            }else{
              const line=document.createElement('p');line.append(document.createTextNode('Account ID: '));
              const id=document.createElement('code');id.textContent=result.account_id;line.append(id);results.append(line);
            }
            if(result.exact_match===true&&(result.source==='active'||result.accounts_only_ready===true)){
              const add=document.createElement('button');add.type='button';
              add.textContent='Добавить этот account в CRM';
              add.onclick=async()=>{
                add.disabled=true;message.textContent=text.waiting;
                try{
                  const saved=await request('/api/platform/operator/accounts',{
                    action:'add_account_company',account_id:result.account_id});
                  if(current!==revision||requestId!==directoryRevision)return;
                  const outcome=saved.account_company||{};
                  if(outcome.status==='manual_review'){
                    message.textContent='Для account требуется ручная проверка: локальная компания уже существует или связь неоднозначна. Новая запись и переназначение не выполнялись.';
                  }else if(outcome.status==='create'){
                    message.textContent='CRM-компания создана. Её название — только account ID; телефон, email и описание оставлены пустыми. Данные в Operator не изменялись.';
                  }else if(outcome.status==='reuse'||outcome.status==='bind'){
                    message.textContent='Существующая CRM-компания сохранена без изменения её полей.';
                  }else{
                    message.textContent='Не удалось определить результат добавления. Проверьте компанию в CRM перед повторной попыткой.';
                  }
                }catch(error){if(current===revision&&requestId===directoryRevision)message.textContent=error.message;}
                finally{if(current===revision&&requestId===directoryRevision)add.disabled=false;}
              };
              results.append(add);
            }
            const note=document.createElement('p');note.textContent='Показана одна запись максимум. Результат не является полным каталогом.';results.append(note);
            message.textContent='Проверка /user/me пройдена; использовано '+(result.source==='active'?'активное подключение':'pending JWT')+'.';
          }else{
            results.textContent='Совпадений не найдено. Проверка /user/me пройдена; показанная строка поиска не охватывает весь каталог.';
            message.textContent='Использовано '+(result.source==='active'?'активное подключение':'pending JWT')+'.';
          }
        }catch(error){if(current===revision&&requestId===directoryRevision){results.textContent='';message.textContent=error.message;}}
        finally{if(current===revision&&requestId===directoryRevision)button.disabled=false;}
        return;
      }
      button.disabled=true;message.textContent=text.waiting;
      try {
        const found=await request('/api/platform/operator/accounts',{search:value});
        if(current!==revision||requestId!==directoryRevision)return;
        results.replaceChildren();
        for(const row of found.accounts||[]){
          const item=document.createElement('button');item.type='button';item.textContent=row.account;
          item.onclick=()=>{$(panel,'[data-account]').value=row.account;};results.append(item);
        }
        message.textContent=(found.accounts||[]).length?'':'Аккаунты не найдены.';
      } catch(error) { if(current===revision&&requestId===directoryRevision)message.textContent=error.message; }
      finally { if(current===revision&&requestId===directoryRevision)button.disabled=false; }
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
