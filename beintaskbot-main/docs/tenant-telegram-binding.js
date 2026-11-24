(() => {
  'use strict';
  let scope = '', card = null, pending = null, hydrated = false, observer = null;
  const call = async (method, signal) => {
    const response = await fetch('/api/platform/telegram/binding', {method, signal,
      credentials: 'same-origin', cache: 'no-store',
      headers: method === 'POST' ? {'Content-Type': 'application/json'} : {}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.success) throw Error(data.error || 'Telegram unavailable');
    return data;
  };
  function visible() {
    return card?.isConnected && !card.hidden &&
      !!card.closest('#view-profile.active') &&
      card.closest('#profile-company-settings')?.querySelector('[data-settings-tab=notifications][aria-selected=true]');
  }
  async function refresh() {
    if (!visible() || hydrated || pending) return;
    const current = card, operation = pending = new AbortController();
    const state = current.querySelector('[data-state]'), button = current.querySelector('[data-bind]');
    try {
      const data = await call('GET', operation.signal);
      if (card !== current || operation.signal.aborted) return;
      state.textContent = data.bound ?
        'Telegram qoşulub. AI məsləhət bildirişləri bu hesab üçün göndərilə bilər.' :
        'Telegram qoşulmayıb. Bildirişlər yalnız kabinetdə görünəcək.';
      button.hidden = !!data.bound;
      button.disabled = false;
    } catch (error) {
      if (card === current && !operation.signal.aborted) state.textContent = error.message;
    } finally {
      if (pending === operation) { pending = null; hydrated = true; }
    }
  }
  function show(profile) {
    const member = profile?.member;
    const identity = member?.tenant_id && member.telegram_id && member.active !== false ?
      JSON.stringify([member.tenant_id, member.telegram_id]) : '';
    if (identity !== scope) {
      pending?.abort(); pending = null; hydrated = false;
      observer?.disconnect(); observer = null;
      card?.remove(); card = null; scope = identity;
    }
    const host = document.getElementById('profile-company-settings');
    if (!scope || card || !host) return;
    const current = card = document.createElement('article');
    current.id = 'telegramBinding';
    current.className = 'panel';
    current.hidden = true;
    current.innerHTML = '<h2>Telegram bildirişləri</h2><p class="sub" data-state>Yoxlanılır…</p><button class="outline" type="button" data-bind disabled>Telegramı qoş</button>';
    host.append(current);
    observer = new MutationObserver(refresh);
    observer.observe(current, {attributes:true,attributeFilter:['hidden']});
    const state = current.querySelector('[data-state]'), button = current.querySelector('[data-bind]');
    button.onclick = async () => {
      if (card !== current || !visible() || pending || button.disabled) return;
      const operation = pending = new AbortController();
      button.disabled = true;
      try {
        const data = await call('POST', operation.signal);
        if (card !== current || operation.signal.aborted || !visible()) return;
        if (!data.deep_link) throw Error('Bot link is unavailable. Try again after the bot starts.');
        window.open(data.deep_link, '_blank', 'noopener');
        state.textContent = 'Telegramda açılan bir dəfəlik linki təsdiqləyin. Link 15 dəqiqə qüvvədədir.';
      } catch (error) {
        if (card === current && !operation.signal.aborted) state.textContent = error.message;
      } finally {
        if (pending === operation) { pending = null; button.disabled = false; }
      }
    };
    refresh();
  }
  document.addEventListener('tenant-profile', event => show(event.detail));
  document.addEventListener('tenant-view', refresh);
  for (const type of ['tenant-auth-invalid', 'tenant-auth-pending'])
    document.addEventListener(type, () => show(null));
  show(window.tenantProfileSnapshot);
})();
