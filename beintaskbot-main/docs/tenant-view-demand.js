/* Page/session-local UI retention. Background workers and API authorization are unchanged. */
(() => {
  const subscriptions = new Set(), inertNodes = new Map();
  let profile = window.tenantProfileSnapshot || null, identity = '', generation = 0, bootstrap;
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
    binding(data)?.can_review_news === true]));
  const snapshot = data => JSON.stringify([data?.member, data?.integrations]);
  const newsAvailable = data => !!(data?.member?.active && data?.capabilities?.modules?.linear && (
    data.member.role === 'owner' || binding(data)?.can_review_news === true));
  const protectedSelector = '#view-news,#tnModal,#chakra-settings-root,#chakra-integrations-root,' +
    '#integrationMarketplaceDialog,#tenantLinearPanel,#tcAiPanel,#tnAiPanel,#tnChannelPanel';
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
    item.started = 0; item.dirty = false; item.reset();
    if (data) { item.freshness = snapshot(data); item.prepare?.(data); }
  }
  function refresh(item) {
    if (!profile || paused) return;
    const visible = item.provider ? integration === item.provider :
      document.getElementById('view-' + item.view)?.classList.contains('active') &&
      (!item.host || !document.getElementById(item.host)?.hidden);
    if (!visible) { item.visible = false; return; }
    // Never replace an edited form just because the user returned or /me refreshed.
    if (!item.visible && item.started && !dirty(item) && Date.now() - item.started >= 30000) reset(item, profile);
    item.visible = true;
    if (!item.started) { item.started = Date.now(); item.activate(profile);
      if (item.provider) document.dispatchEvent(new CustomEvent('tenant-integration-panel-ready', {detail: {provider: item.provider}}));
    }
  }
  function apply(data) {
    if (!data?.member) return;
    const next = fingerprint(data), changed = next !== identity, reauthorized = paused;
    profile = data; identity = next;
    // Keep the curtain in place until old-context roots and callbacks have been reset.
    for (const item of subscriptions) {
      if (changed || (!dirty(item) && (reauthorized || item.freshness !== snapshot(data)))) reset(item, data);
    }
    conceal(false);
    for (const item of subscriptions) refresh(item);
  }
  function invalidate() {
    generation++; profile = null; identity = ''; integration = '';
    for (const item of subscriptions) { item.visible = false; reset(item); }
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
    if (profile) { identity = fingerprint(profile); item.freshness = snapshot(profile); item.prepare?.(profile); refresh(item); }
    else if (!bootstrap && !paused) {
      const current = generation;
      bootstrap = fetch('/api/platform/me', {credentials: 'same-origin', cache: 'no-store'})
        .then(response => response.ok ? response.json() : null)
        .then(data => { if (current === generation && !profile) apply(data); }).catch(() => {})
        .finally(() => { bootstrap = null; });
    }
  }
  window.tenantViewDemand = {watch, newsAvailable, clean: selector => {
    for (const item of subscriptions) if (item.editable === selector) item.dirty = false;
  }};
})();
