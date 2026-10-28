/* Page/session-local UI retention. Background workers and API authorization are unchanged. */
(() => {
  const subscriptions = new Set();
  let profile = window.tenantProfileSnapshot || null, identity = '', generation = 0, bootstrap;
  let integration = '';
  const fingerprint = data => JSON.stringify([data?.member, data?.capabilities, data?.role_permissions, data?.integrations]);
  const newsAvailable = data => !!(data?.member?.active && data?.capabilities?.modules?.linear && (
    data.member.role === 'owner' || (data.integrations || []).find(row => row.provider === 'linear')
      ?.metadata?.settings?.members?.[String(data.member.telegram_id)]?.can_review_news === true));
  function refresh(item) {
    if (!profile) return;
    const visible = item.provider ? integration === item.provider :
      document.getElementById('view-' + item.view)?.classList.contains('active') &&
      (!item.host || !document.getElementById(item.host)?.hidden);
    if (!visible) { item.visible = false; return; }
    // A brief return keeps rendered data/forms; an older visit reloads through the existing reader.
    if (!item.visible && item.started && Date.now() - item.started >= 30000) {
      item.started = 0; item.reset(); item.prepare?.(profile);
    }
    item.visible = true;
    if (!item.started) { item.started = Date.now(); item.activate(profile); }
  }
  function apply(data) {
    if (!data?.member) return;
    const next = fingerprint(data), changed = next !== identity;
    profile = data; identity = next;
    for (const item of subscriptions) {
      if (changed) { item.reset(); item.started = 0; item.prepare?.(data); }
      refresh(item);
    }
  }
  function invalidate() {
    generation++; profile = null; identity = ''; integration = '';
    for (const item of subscriptions) { item.started = 0; item.visible = false; item.reset(); }
  }
  document.addEventListener('tenant-profile', event => apply(event.detail));
  document.addEventListener('tenant-auth-pending', invalidate);
  document.addEventListener('tenant-auth-invalid', invalidate);
  document.addEventListener('tenant-view', () => { for (const item of subscriptions) refresh(item); });
  document.addEventListener('tenant-integration-panel', event => {
    integration = event.detail?.open ? event.detail.provider : '';
    for (const item of subscriptions) refresh(item);
  });
  function watch(options) {
    const item = {...options, reset: options.reset || (() => {}), started: 0, visible: false};
    subscriptions.add(item);
    const observer = new MutationObserver(() => refresh(item));
    for (const id of ['view-' + item.view, item.host].filter(Boolean)) {
      const node = document.getElementById(id);
      if (node) observer.observe(node, {attributes: true, attributeFilter: ['class', 'hidden']});
    }
    if (profile) { identity = fingerprint(profile); item.prepare?.(profile); refresh(item); }
    else if (!bootstrap) {
      const current = generation;
      bootstrap = fetch('/api/platform/me', {credentials: 'same-origin', cache: 'no-store'})
        .then(response => response.ok ? response.json() : null)
        .then(data => { if (current === generation && !profile) apply(data); }).catch(() => {})
        .finally(() => { bootstrap = null; });
    }
  }
  window.tenantViewDemand = {watch, newsAvailable};
})();
