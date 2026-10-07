/* Creation review queue in /app; legacy confirmations are not modified. */
(() => {
  const host = document.getElementById('view-tasks');
  const panel = document.createElement('section');
  panel.className = 'panel'; panel.hidden = true; panel.style.marginTop = '20px';
  panel.innerHTML = '<div style="display:flex;gap:12px;align-items:center;justify-content:space-between"><h2>Tapşırıq təsdiqləri</h2><button type="button" class="outline">Yenilə</button></div><p class="sub">Kommo-da yaradılmazdan əvvəl administrator təsdiqi gözləyən sorğular.</p><div class="approval-notice" role="status"></div><div class="approval-list"></div>';
  host.append(panel);
  const refresh = panel.querySelector('button');
  const notice = panel.querySelector('.approval-notice');
  const list = panel.querySelector('.approval-list');
  let generation = 0;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(data) {
    const response = await fetch('/api/platform/crm/task-approvals', {credentials:'same-origin',
      method: data ? 'POST' : 'GET', headers:{'Content-Type':'application/json'},
      ...(data ? {body:JSON.stringify(data)} : {})});
    const result = await response.json();
    if (!response.ok || !result.success) throw Error(result.error || 'Sorğu alınmadı.');
    return result;
  }
  async function load() {
    const current = ++generation; refresh.disabled = true;
    try {
      const result = await api();
      if (current !== generation || panel.hidden) return;
      list.replaceChildren();
      for (const item of result.approvals || []) {
        const card = document.createElement('article'); card.className = 'member';
        card.style.cssText = 'display:block;padding:16px 0;border-bottom:1px solid var(--line)';
        const task = item.task || {};
        const date = new Date(task.due_at);
        const due = Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('az-AZ');
        card.innerHTML = `<b>${esc(item.creator_name)}</b><p style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(task.text)}</p><p class="sub">İcraçı: ${esc(item.executor_name)} · ${task.lead_id ? 'Sövdələşmə #'+esc(task.lead_id) : 'Sövdələşməsiz'} · ${esc(due)}</p>`;
        if (item.step !== 'waiting_approval') {
          const text = document.createElement('p'); text.textContent = 'Əməliyyat başlayıb. Kommo nəticəsini yoxlayın. Sorğu: '+item.request_id;
          card.append(text);
        } else {
          const actions = document.createElement('div'); actions.style.cssText = 'display:flex;gap:10px;flex-wrap:wrap';
          for (const [action, label, className] of [['approve','Təsdiqlə','primary'],['reject','Rədd et','outline danger']]) {
            const button = document.createElement('button'); button.type = 'button'; button.className = className; button.textContent = label;
            button.onclick = async () => {
              actions.querySelectorAll('button').forEach(b => b.disabled = true); button.textContent = 'Gözləyin…'; notice.textContent = '';
              try {
                await api({creator_id:item.creator_id, request_id:item.request_id, action});
                notice.textContent = action === 'approve' ? 'Tapşırıq yaradıldı.' : 'Sorğu rədd edildi.';
                await load(); document.getElementById('tasksRefresh').click();
              } catch (error) {
                notice.textContent = error.message;
                await load(); // Re-read durable state, never retry a provider write blindly.
              }
            };
            actions.append(button);
          }
          card.append(actions);
        }
        list.append(card);
      }
      if (!list.children.length) list.textContent = 'Təsdiq gözləyən tapşırıq yoxdur.';
    } catch (error) { if (current === generation) notice.textContent = error.message; }
    finally { if (current === generation) refresh.disabled = false; }
  }
  refresh.onclick = load;
  function apply(data) {
    panel.hidden = !(['owner','admin'].includes(data.member?.role) && data.capabilities?.modules?.tasks);
    if (panel.hidden) { generation++; list.replaceChildren(); notice.textContent = ''; }
    else load();
  }
  document.addEventListener('tenant-profile', event => apply(event.detail));
  fetch('/api/platform/me', {credentials:'same-origin'}).then(response => response.json())
    .then(data => { if (data.success) apply(data); }).catch(() => {});
})();
