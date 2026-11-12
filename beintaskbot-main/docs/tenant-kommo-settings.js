/* Owner-only manual Kommo connection. Long-lived tokens are never retained in the page. */
(() => {
  const $ = id => document.getElementById(id);
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  let accessData, profile, generation = 0, busy = false, employeeRows = [];

  async function api(url, data) {
    if(data)data=window.tenantSharedIntegrations.command(accessData,'kommo',data,url.endsWith('/disconnect')?'delete':window.tenantSharedIntegrations.connected(accessData,'kommo')?'update':'create');
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      ...(data ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) } : {}),
    });
    const result = await response.json();
    if (!response.ok || !result.success) throw new Error(result.error || "Kommo ayarları yüklənmədi.");
    if(data)window.tenantSharedIntegrations.record(accessData,'kommo',{status:url.endsWith('/disconnect')?'not_connected':'connected'});
    return result;
  }

  async function memberApi(data) {
    const response = await fetch('/api/platform/members', {
      credentials: 'same-origin', cache: 'no-store',
      ...(data ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) } : {}),
    });
    const result = await response.json();
    if (!response.ok || !result.success) throw new Error(result.error || 'Əməkdaş ayarları yüklənmədi.');
    return result;
  }

  function employeeCard(row) {
    const settings = row?.kommo_settings;
    if (!settings?.version) return '';
    const options = [...(settings.pipelines || [])];
    for (const id of settings.values.pipeline_ids || []) if (!options.some(item => item.pipeline_id === id))
      options.push({ pipeline_id: id, name: `${id} (inactive)` });
    const editable = profile?.role === 'owner' && row.active === true;
    return `<details data-kommo-employee="${escapeHtml(row.telegram_id)}" style="margin:12px 0;padding:12px;border:1px solid var(--line);border-radius:12px">
      <summary style="cursor:pointer;font-weight:650">${escapeHtml(row.display_name || 'Əməkdaş')} · ${row.active ? 'Aktiv' : 'Deaktiv'}</summary>
      <p>Əməkdaş seçilmiş vərəqlərdəki sövdələşmələri və özü yaratdığı qeydləri görür. Tapşırıqlar onun əlçatan sövdələşmələrinə və ya öz yaratdığı tapşırıqlara əsasən görünür.</p>
      <label>Kommo vərəqlərinə giriş<select multiple data-kommo-pipelines ${editable ? '' : 'disabled'} style="min-height:100px">${options.map(item => `<option value="${escapeHtml(item.pipeline_id)}" ${(settings.values.pipeline_ids || []).includes(item.pipeline_id) ? 'selected' : ''}>${escapeHtml(item.name || item.pipeline_id)}</option>`).join('')}</select></label>
      <label>Kommo istifadəçi ID-si<input data-kommo-user-id inputmode="numeric" pattern="[0-9]*" value="${escapeHtml(settings.values.kommo_user_id || '')}" ${editable ? '' : 'disabled'}></label>
      <button type="button" class="outline" data-kommo-save ${editable ? '' : 'disabled'}>Bu əməkdaşın Kommo ayarlarını saxla</button>
      <p data-kommo-result role="status" aria-live="polite"></p></details>`;
  }

  function bindEmployeeCard(root, row) {
    root.querySelector('[data-kommo-save]')?.addEventListener('click', async () => {
      if (busy || profile?.role !== 'owner' || !row.active || !root.isConnected) return;
      const status = root.querySelector('[data-kommo-result]'), settings = row.kommo_settings;
      const pipeline_ids = [...root.querySelector('[data-kommo-pipelines]').selectedOptions].map(option => option.value);
      const kommo_user_id = root.querySelector('[data-kommo-user-id]').value.trim();
      const changes = {};
      if (JSON.stringify(pipeline_ids) !== JSON.stringify(settings.values.pipeline_ids || [])) changes.pipeline_ids = pipeline_ids;
      if (kommo_user_id !== (settings.values.kommo_user_id || '')) changes.kommo_user_id = kommo_user_id;
      if (!Object.keys(changes).length) { status.textContent = 'Dəyişiklik yoxdur.'; return; }
      let failed=false;
      const current = generation, identity = profile.identity;
      const valid = () => current === generation && profile?.identity === identity && root.isConnected;
      disable(true); status.textContent = 'Saxlanılır…';
      try {
        const result = await memberApi({ action: 'update_member_card', telegram_id: row.telegram_id,
          expected_tenant_id: profile.tenant_id, expected_user_id: profile.telegram_id,
          expected_version: row.card_version, changes: { kommo_settings: { expected_version: settings.version, changes } } });
        if (!valid()) return;
        if (!result.applied || String(result.applied.telegram_id) !== String(row.telegram_id)) throw new Error('Saxlama nəticəsi təsdiqlənmədi.');
        status.textContent = 'Saxlanıldı. Ayarlar yenilənir…';
        const fresh = await memberApi(); if (!valid()) return;
        employeeRows = fresh.members || [];
        const updated = employeeRows.find(item => String(item.telegram_id) === String(row.telegram_id));
        if (!updated?.kommo_settings?.version) throw new Error('Əməkdaşın yeni ayarları yüklənmədi.');
        const holder = document.createElement('div'); holder.innerHTML = employeeCard(updated);
        const replacement = holder.firstElementChild; root.replaceWith(replacement); bindEmployeeCard(replacement, updated);
        replacement.querySelector('[data-kommo-result]').textContent = 'Kommo ayarları saxlanıldı.';
      } catch (error) { failed=true;if (valid()) { status.textContent = `${error.message} Davam etmək üçün səhifəni yeniləyin.`; root.querySelectorAll('button,input,select').forEach(node => node.disabled = true); } } finally { if(current===generation){disable(false);if(failed&&root.isConnected)root.querySelectorAll('button,input,select').forEach(node=>node.disabled=true);} }
    });
  }

  function renderEmployees() {
    const host = $('tkEmployees'); if (!host) return;
    host.innerHTML = '<h3>Əməkdaşlar üzrə Kommo girişi</h3>' + (employeeRows.length
      ? employeeRows.map(employeeCard).join('') : '<p>Əməkdaş tapılmadı.</p>');
    host.querySelectorAll('[data-kommo-employee]').forEach(root => {
      const row = employeeRows.find(item => String(item.telegram_id) === root.dataset.kommoEmployee);
      if (row) bindEmployeeCard(root, row);
    });
  }

  function disable(on) {
    busy = on;
    $("tenantKommoPanel")?.querySelectorAll("#tkLoad,#tkConnect,#tkDisconnect,#tkDomain,#tkToken,#tkConfirm").forEach(element => { element.disabled = on;
      const shared=window.tenantSharedIntegrations;
      if(shared.scoped(accessData,'kommo')){
        const action=element.id==='tkDisconnect'?'delete':element.id==='tkLoad'?'view':shared.connected(accessData,'kommo')?'update':'create';
        element.disabled=on||!shared.can(accessData,'kommo',action);
      } });
    $("tkEmployees")?.querySelectorAll('[data-kommo-pipelines],[data-kommo-user-id],[data-kommo-save]').forEach(element => {
      const row=employeeRows.find(item=>String(item.telegram_id)===element.closest('[data-kommo-employee]')?.dataset.kommoEmployee);
      element.disabled=on||profile?.role!=='owner'||row?.active!==true;
    });
  }

  async function perform(work) {
    if (busy) return;
    const current = generation;
    disable(true);
    $("tkMessage").textContent = "Yüklənir…";
    try {
      await work(current);
      if (current === generation) $("tkMessage").textContent = "Hazırdır.";
    } catch (error) {
      if (current === generation) $("tkMessage").textContent = error.message;
    } finally {
      if (current === generation) disable(false);
    }
  }

  async function load(current) {
    $("tkBody").hidden = false;
    $("tkToken").value = "";
    $("tkConfirm").checked = false;
    if (profile?.role === 'owner') {
        try { const employees = await memberApi(); if (current !== generation) return; employeeRows = employees.members || []; renderEmployees(); }
        catch (error) { if(current!==generation)return;if($('tkEmployees'))$('tkEmployees').innerHTML = `<p role="alert">${escapeHtml(error.message)}</p>`; }
      }
    try {
      if(window.tenantSharedIntegrations.scoped(accessData,'kommo')&&!window.tenantSharedIntegrations.can(accessData,'kommo','view')) { $('tkStatus').textContent=window.tenantSharedIntegrations.row(accessData,'kommo').status; $('tkDisconnect').hidden=!window.tenantSharedIntegrations.can(accessData,'kommo','delete');disable(false);return; }
      const result = await api("/api/platform/integrations/kommo/test");
      if (current !== generation) return;
      if (result.auth_mode === "oauth") {
        $("tkStatus").textContent = "Kommo-nun rəsmi bağlantısı aktivdir · " + (result.account?.name || "Kommo hesabı");
        $("tkManual").hidden = true;
        $("tkDisconnect").hidden = true;
      } else {
        $("tkStatus").textContent = "Əl ilə Kommo bağlantısı işləyir · " + (result.account?.name || "Kommo hesabı");
        $("tkManual").hidden = false;
        $("tkDisconnect").hidden = false;
      }
    } catch (error) {
      if (current !== generation) return;
      $("tkStatus").textContent = error.message || "Kommo hələ qoşulmayıb.";
      $("tkManual").hidden = false;
      $("tkDisconnect").hidden = true;
    }
  }

  function install(data) {
    accessData = data;
    const next = data.member;
    const identity = next.tenant_id + ":" + next.telegram_id + ":" + next.role;
    if (profile?.identity === identity) return;
    generation++;
    profile = { ...next, identity };
    employeeRows = [];
    window.tenantViewDemand.clean("#tenantKommoPanel");
    $("tenantKommoPanel")?.remove();
    if (window.tenantSharedIntegrations.scoped(data,"kommo") ? !Object.values(window.tenantSharedIntegrations.row(data,"kommo").management_actions).some(Boolean) : next.role !== "owner") return;
    const settings = $("integrationSettingsHost");
    if (!settings) return;

    const panel = document.createElement("article");
    panel.className = "panel";
    panel.id = "tenantKommoPanel";
    panel.style.marginTop = "16px";
    panel.innerHTML = [
      "<h2>Kommo · əl ilə bağlantı</h2>",
      "<p>Rəsmi OAuth bağlantısı hazır olana qədər şirkət hesabını long-lived token ilə qoşa bilərsiniz.</p>",
      '<button class="outline" id="tkLoad" type="button">Bağlantını yoxla</button>',
      '<div id="tkBody" hidden>',
      '<p id="tkStatus" role="status"></p>',
      '<div id="tkEmployees"></div>',
      '<div id="tkCrmSyncHost"></div>',
      '<div id="tkManual">',
      '<p>Mövcud şəxsi Kommo inteqrasiyanızın long-lived tokenindən istifadə edin; yenisini yaratmağa ehtiyac yoxdur. Token güclü giriş hüquqlarına malik ola bilər və Kommo-da ləğv edilə bilər. Token yalnız Kommo hesabında yoxlanılır, serverdə şifrəli saxlanır və yenidən göstərilmir. Tokeni söhbətdə göndərməyin.</p>',
      '<p><a href="https://developers.kommo.com/docs/long-lived-token" target="_blank" rel="noopener noreferrer">Kommo long-lived token təlimatı</a></p>',
      '<label>Kommo hesab ünvanı<input id="tkDomain" type="text" autocomplete="url" placeholder="şirkətiniz.kommo.com"></label>',
      '<label>Long-lived token<input id="tkToken" type="password" autocomplete="new-password" placeholder="Yeni token (saxlanmış token göstərilmir)"></label>',
      '<label style="display:flex;align-items:flex-start"><input id="tkConfirm" type="checkbox" style="width:auto;margin-right:8px">Tokenin Kommo icazələrini bu şirkət adından istifadə etməsinə razıyam.</label>',
      '<button class="outline" id="tkConnect" type="button">Yoxla və qoş</button>',
      "</div>",
      '<button class="outline danger" id="tkDisconnect" type="button" hidden>Əl ilə bağlantını ayır</button>',
      "</div>",
      '<p id="tkMessage" role="status" aria-live="polite"></p>',
    ].join("");
    settings.appendChild(panel);

    $("tkLoad").onclick = () => perform(load);
    $("tkConnect").onclick = () => perform(async current => {
      const token = $("tkToken").value;
      const accountDomain = $("tkDomain").value.trim();
      if (!token) throw new Error("Kommo long-lived tokenini daxil edin.");
      if (!$("tkConfirm").checked) throw new Error("Token icazələrini təsdiqləyin.");
      $("tkToken").value = "";
      await api("/api/platform/integrations/kommo", {
        action: "manual_connect",
        account_domain: accountDomain,
        access_token: token,
        expected_tenant_id: profile.tenant_id,
        expected_user_id: profile.telegram_id,
        confirm_long_lived_token: true,
      });
      if (current === generation) await load(current);
    });
    $("tkDisconnect").onclick = () => {
      if (!window.confirm("Kommo bağlantısını bu şirkətdən ayırmaq istəyirsiniz?")) return;
      perform(async current => {
        await api("/api/platform/integrations/disconnect", { provider: "kommo_manual" });
        if (current === generation) await load(current);
      });
    };
    void perform(load);
  }

  window.tenantViewDemand.watch({
    provider: "kommo",
    resetOnHide: true,
    editable: "#tenantKommoPanel",
    activate: install,
    reset: () => {
      generation++;
      profile = null;
      busy = false;
      employeeRows = [];
      window.tenantViewDemand.clean("#tenantKommoPanel");
      $("tenantKommoPanel")?.remove();
    },
  });
})();
