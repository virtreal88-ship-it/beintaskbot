/* Page/session-local UI retention. Background workers and API authorization are unchanged. */
(() => {
  const subscriptions = new Set(), inertNodes = new Map();
  let profile = window.tenantProfileSnapshot || null, generation = 0, bootstrap;
  let integration = '', paused = false;
  const binding = data => (Array.isArray(data?.integrations) ? data.integrations : []).find(row => row?.provider === 'linear')
    ?.metadata?.settings?.members?.[String(data?.member?.telegram_id)];
  // Display names, timestamps and integration status are freshness, not identity.
  const stable = value => Array.isArray(value) ? value.map(stable) : value && typeof value === 'object' ?
    Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])])) : value;
  const fingerprint = data => JSON.stringify(stable([data?.member?.tenant_id, data?.member?.telegram_id,
    data?.member?.role, data?.member?.active, [...(data?.member?.permissions || [])].sort(),
    Object.fromEntries(Object.entries(data?.capabilities || {}).filter(([key]) => !['deal_board', 'notification_events'].includes(key))),
    Object.fromEntries(Object.entries(data?.role_permissions || {}).map(([key, value]) => [key, Array.isArray(value) ? [...value].sort() : value])),
    binding(data)?.can_review_news === true, (Array.isArray(data?.integrations) ? data.integrations : []).map(row => [row.provider, row.management_actions, row.status_only])]));
  const snapshot = data => JSON.stringify([data?.member, data?.integrations]);
  const employees = item => item.host === 'staffManagement';
  // Employee drafts do not depend on integration health or news-review bindings.
  // Keep their security scope explicit, and their data freshness separate.
  const employeeIdentity = data => JSON.stringify(stable([data?.member?.tenant_id, data?.member?.telegram_id,
    data?.member?.role, data?.member?.active, [...(data?.member?.permissions || [])].sort(),
    data?.capabilities?.can_manage_employees, data?.capabilities?.can_view_employees, data?.capabilities?.can_update_employees, data?.capabilities?.can_create_employees, data?.member?.department_id, data?.capabilities?.pages?.employees,
    data?.capabilities?.actions?.employees,
    Object.fromEntries(Object.entries(data?.role_permissions || {}).map(([key, value]) => [key, Array.isArray(value) ? [...value].sort() : value]))]));
  const memberData = member => member && Object.fromEntries(Object.entries(member).filter(([key]) =>
    !['created_at', 'updated_at', 'last_login_at', 'account_last_login_at', 'last_seen_at'].includes(key))
    .map(([key, value]) => [key, key === 'permissions' && Array.isArray(value) ? [...value].sort() : value]));
  const itemIdentity = (item, data) => employees(item) ? employeeIdentity(data) : fingerprint(data);
  const freshness = (item, data) => employees(item) ? JSON.stringify(stable([
    memberData(data?.member), Array.isArray(data?.members) ? data.members.map(memberData) : undefined
  ])) : snapshot(data);
  const newsAvailable = data => !!(data?.member?.active === true && data.member.tenant_id && data.member.telegram_id
    && (data.member.tenant_status || 'active') === 'active' && data?.capabilities?.pages?.news !== false);
  const newsReviewAvailable = data => !!(newsAvailable(data) && data?.capabilities?.modules?.linear && (
    data.member.role === 'owner' || binding(data)?.can_review_news === true));
  const protectedSelector = '#view-news,#tnModal,#chakra-settings-root,#chakra-notification-settings-root,#chakra-integrations-root,' +
    '#integrationMarketplaceDialog,#tenantLinearPanel,#tenantKommoPanel,#tcAiPanel,#tnAiPanel,#tnChannelPanel';
  const style = document.createElement('style');
  style.textContent = protectedSelector.split(',').map(selector =>
    `html[data-tenant-panels-pending] ${selector}`).join(',') + '{visibility:hidden!important;pointer-events:none!important}';
  document.head.append(style);
  function conceal(on) {
    paused = on;
    document.documentElement.toggleAttribute('data-tenant-panels-pending', on);
    if (on) document.querySelectorAll(protectedSelector).forEach(node => {
      if (!inertNodes.has(node)) inertNodes.set(node, node.hasAttribute('inert'));
      node.setAttribute('inert', '');
    });
    else { for (const [node, wasInert] of inertNodes) node.toggleAttribute('inert', wasInert); inertNodes.clear(); }
  }
  for (const type of ['click', 'input', 'change', 'keydown', 'submit', 'focusin']) {
    document.addEventListener(type, event => {
      if (paused && event.target.closest?.(protectedSelector)) {
        event.preventDefault(); event.stopImmediatePropagation();
        if (type === 'focusin') event.target.blur?.();
        return;
      }
      if (type === 'input' || type === 'change') {
        for (const item of subscriptions) if (item.editable && event.target.closest?.(item.editable) && !(item.excludeEditable && event.target.closest?.(item.excludeEditable))) item.dirty = true;
      }
    }, true);
  }
  const dirty = item => item.dirty || (item.preserveProviderDrafts && [...subscriptions].some(other => other.provider === integration && other.dirty));
  function reset(item, data) {
    if (item.resetting) return;
    item.resetting = true;
    try {
      item.started = 0; item.dirty = false; item.reset();
      if (data) { item.freshness = freshness(item, data); item.prepare?.(data); }
    } finally { item.resetting = false; }
  }
  function refresh(item) {
    if (!profile || paused || item.resetting) return;
    const visible = item.provider ? integration === item.provider :
      document.getElementById('view-' + item.view)?.classList.contains('active') &&
      (!item.host || !document.getElementById(item.host)?.hidden);
    if (!visible) { item.visible = false; if (item.resetOnHide && item.started) reset(item, profile); return; }
    // Never replace an edited form just because the user returned or /me refreshed.
    if (!item.visible && item.started && !dirty(item) && Date.now() - item.started >= 30000) reset(item, profile);
    item.visible = true;
    if (!item.started) { item.started = Date.now(); item.activate(profile);
      if (item.provider) document.dispatchEvent(new CustomEvent('tenant-integration-panel-ready', {detail: {provider: item.provider}}));
    }
  }
  function apply(data) {
    if (!data?.member) return;
    const reauthorized = paused;
    profile = data;
    // Keep the curtain in place until old-context roots and callbacks have been reset.
    for (const item of subscriptions) {
      const next = itemIdentity(item, data), changed = next !== item.identity;
      item.identity = next;
      if (changed || (!dirty(item) && ((reauthorized && !employees(item)) || item.freshness !== freshness(item, data)))) reset(item, data);
    }
    conceal(false);
    for (const item of subscriptions) {
      // The employee controller lifts its auth curtain later in this same
      // profile event. Do not mistake that temporary hidden host for leaving
      // the view, then expire a still-open card when it becomes visible again.
      if (reauthorized && employees(item)) queueMicrotask(() => refresh(item));
      else refresh(item);
    }
  }
  function invalidate() {
    generation++; profile = null; integration = '';
    for (const item of subscriptions) { item.identity = undefined; item.visible = false; reset(item); }
    conceal(false);
  }
  document.addEventListener('tenant-profile', event => apply(event.detail));
  document.addEventListener('tenant-auth-pending', () => { generation++; conceal(true); });
  document.addEventListener('tenant-auth-invalid', invalidate);
  document.addEventListener('tenant-view', () => { for (const item of subscriptions) refresh(item); });
  document.addEventListener('tenant-integration-panel', event => {
    integration = event.detail?.open ? event.detail.provider : '';
    for (const item of subscriptions) refresh(item);
  });
  function watch(options) {
    const item = {...options, reset: options.reset || (() => {}), started: 0, visible: false, dirty: false};
    subscriptions.add(item);
    const observer = new MutationObserver(() => refresh(item));
    for (const id of ['view-' + item.view, item.host].filter(Boolean)) {
      const node = document.getElementById(id);
      if (node) observer.observe(node, {attributes: true, attributeFilter: ['class', 'hidden']});
    }
    if (profile) { item.identity = itemIdentity(item, profile); item.freshness = freshness(item, profile); item.prepare?.(profile); refresh(item); }
    else if (!bootstrap && !paused) {
      const current = generation;
      bootstrap = fetch('/api/platform/me', {credentials: 'same-origin', cache: 'no-store'})
        .then(response => response.ok ? response.json() : null)
        .then(data => { if (current === generation && !profile) apply(data); }).catch(() => {})
        .finally(() => { bootstrap = null; });
    }
  }
  window.tenantViewDemand = {watch, newsAvailable, newsReviewAvailable, clean: selector => {
    for (const item of subscriptions) if (item.editable === selector) item.dirty = false;
  }};
})();
