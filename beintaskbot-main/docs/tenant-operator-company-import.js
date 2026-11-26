/* One-way Operator company catalogue import. The queue survives closing the page. */
(() => {
  const host = document.getElementById('view-companies');
  const list = document.getElementById('nativeCompaniesList');
  if (!host || !list) return;
  const copy = {
    ru: ['Импорт из Operator','Предпросмотр импорта','Подтвердить импорт','Отмена','Проверяем…',
      'Получено','Уже есть','К импорту','Конфликты','Создано','Импорт выполняется','Импорт завершён',
      'Импорт остановлен. Проверьте подключение и откройте новый предпросмотр.',
      'Operator вернул предельное число записей. Полнота списка не подтверждена.'],
    az: ['Operator-dan idxal','İdxalı önizlə','İdxalı təsdiqlə','Ləğv et','Yoxlanılır…',
      'Alınıb','Artıq mövcuddur','İdxal olunacaq','Uyğunsuzluqlar','Yaradılıb','İdxal davam edir','İdxal tamamlandı',
      'İdxal dayandırıldı. Bağlantını yoxlayıb yeni önizləmə açın.',
      'Operator maksimum sayda qeyd qaytardı. Siyahının tamlığı təsdiqlənməyib.'],
    en: ['Import from Operator','Preview import','Confirm import','Cancel','Checking…',
      'Received','Already present','To import','Conflicts','Created','Import in progress','Import complete',
      'Import stopped. Check the connection and open a new preview.',
      'Operator returned the maximum number of records. A complete catalogue has not been verified.'],
  };
  const t = index => (copy[window.tenantI18n?.language] || copy.az)[index];
  const panel = document.createElement('section');
  panel.id = 'operatorCompanyImport'; panel.className = 'panel'; panel.hidden = true;
  panel.innerHTML = '<h3></h3><div class="toolbar"><button type="button" class="outline" data-preview></button><button type="button" class="primary" data-confirm hidden></button><button type="button" class="outline" data-cancel hidden></button></div><p role="status" aria-live="polite"></p>';
  list.before(panel);
  const preview = panel.querySelector('[data-preview]'), confirm = panel.querySelector('[data-confirm]');
  const cancel = panel.querySelector('[data-cancel]'), status = panel.querySelector('[role=status]');
  let identity = '', profile = null, generation = 0, receipt = null, timer = null, latest = null;
  let busy = false, available = false, loaded = false, reported = '';
  const current = stamp => stamp === generation && !!profile && host.classList.contains('active');
  const counts = (labels, amounts) => labels.map((label, i) => t(label) + ': ' + amounts[i]).join(' · ');
  function localize() {
    panel.querySelector('h3').textContent = t(0);
    preview.textContent = t(1); confirm.textContent = t(2); cancel.textContent = t(3);
    if (latest) render(latest);
  }
  function controls() {
    confirm.hidden = !receipt; cancel.hidden = !receipt;
    confirm.disabled = busy; cancel.disabled = busy;
    preview.disabled = busy || latest?.state === 'queued';
  }
  async function request(url, data) {
    const member = profile;
    const response = await fetch(url, {credentials:'same-origin', cache:'no-store',
      ...(data ? {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
        ...data, expected_tenant_id: member.tenant_id, expected_user_id: member.telegram_id,
      })} : {}),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) throw new Error(payload.error || t(12));
    return payload;
  }
  const command = (action, fields = {}) => request('/api/platform/operator/accounts', {action, ...fields});
  function render(job) {
    latest = job;
    if (!job) {controls(); return;}
    let line = (job.state === 'queued' ? t(10) : job.state === 'completed' ? t(11) : t(12));
    line += ': ' + job.processed + ' / ' + job.total + ' · ' + counts([9,6,8], [job.created,job.reused,job.conflicts]);
    if (job.limit_reached) line += '. ' + t(13);
    status.textContent = line;
    controls();
  }
  async function poll(stamp) {
    if (!current(stamp) || !available) return;
    let again = false;
    try {
      const result = (await command('company_import_status')).company_import_job;
      if (!current(stamp)) return;
      render(result);
      if (result && result.state !== 'queued' && reported !== result.id) {
        reported = result.id;
        if (result.created > 0) window.dispatchEvent(new Event('operator-company-import-finished'));
      }
      again = result?.state === 'queued';
    } catch (error) {
      if (!current(stamp)) return;
      status.textContent = error.message;
      again = latest?.state === 'queued';
    }
    clearTimeout(timer);
    if (again) timer = setTimeout(() => poll(stamp), 4000);
  }
  async function show() {
    if (!profile || !host.classList.contains('active')) return;
    const stamp = generation;
    if (!loaded) {
      loaded = true;
      try {
        const flags = (await request('/api/platform/integrations/beinsystems-operator')).integration;
        if (!current(stamp)) {if (stamp === generation) loaded = false; return;}
        available = flags?.first_company_batch_plan_enabled === true && (flags.configured || flags.accounts_only_ready);
      } catch {
        if (stamp === generation) loaded = false;
        return;
      }
    }
    if (!current(stamp) || !available) return;
    panel.hidden = false;
    if (!receipt && !busy) await poll(stamp);
  }
  preview.onclick = async () => {
    const stamp = generation;
    if (!current(stamp) || !available || busy || latest?.state === 'queued') return;
    busy = true; receipt = null; controls(); status.textContent = t(4);
    try {
      const plan = (await command('preview_company_import_all')).company_batch_preview;
      if (!current(stamp)) return;
      receipt = plan.receipt || null;
      status.textContent = counts([5,6,7,8], [plan.received,plan.already,plan.remaining,plan.conflicts.length])
        + (plan.limit_reached ? '. ' + t(13) : '');
    } catch (error) { if (current(stamp)) status.textContent = error.message; }
    finally { if (stamp === generation) {busy = false; controls();} }
  };
  confirm.onclick = async () => {
    const stamp = generation;
    if (!current(stamp) || !receipt || busy) return;
    busy = true; controls(); status.textContent = t(4);
    try {
      const job = (await command('confirm_company_import_all', {receipt})).company_import_job;
      if (!current(stamp)) return;
      receipt = null;
      render(job);
      await poll(stamp);
    } catch (error) { if (current(stamp)) status.textContent = error.message; }
    finally { if (stamp === generation) {busy = false; controls();} }
  };
  cancel.onclick = () => {receipt = null; status.textContent = ''; controls();};
  function install(data) {
    const member = data?.member;
    const owner = member?.role === 'owner' &&
      data.integrations?.some(row => row.provider === 'beinsystems_operator');
    const next = owner ? member.tenant_id + ':' + member.telegram_id : '';
    if (identity !== next) {
      profile = owner ? member : null; identity = next; generation += 1;
      busy = available = loaded = false; receipt = latest = null; reported = '';
      clearTimeout(timer); panel.hidden = true; status.textContent = ''; controls();
    }
    if (!busy && !receipt) void show();
  }
  document.addEventListener('tenant-profile', event => install(event.detail));
  document.addEventListener('tenant-view', event => {
    if (event.detail?.view === 'companies') void show();
    else {clearTimeout(timer); receipt = null; controls(); if (!available) loaded = false;}
  });
  document.addEventListener('tenant-language', localize);
  localize();
  if (window.tenantProfileSnapshot?.member) install(window.tenantProfileSnapshot);
})();
