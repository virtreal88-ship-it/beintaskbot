/* Progress only: CRM lists still use their authorized snapshot endpoints. */
(() => {
  let profile, timer, generation = 0, watching = false;
  const boxes = {};
  for (const [resource, ids] of Object.entries({leads:['dealsNotice','customersNotice'], tasks:['tasksNotice']})) {
    boxes[resource] = ids.map(id => {
      const anchor = document.getElementById(id);
      if (!anchor) return null;
      const box = document.createElement('div');
      box.className = 'notice'; box.setAttribute('role', 'status');
      anchor.after(box); return box;
    }).filter(Boolean);
  }
  function display(resource, text) {
    for (const box of boxes[resource] || []) {
      box.textContent = text; box.classList.toggle('show', !!text);
    }
  }
  function schedule(epoch) {
    clearTimeout(timer);
    if (epoch === generation && watching && !document.hidden) timer = setTimeout(() => poll(epoch), 5000);
  }
  async function poll(epoch) {
    if (!profile || epoch !== generation || document.hidden) return;
    try {
      const response = await fetch('/api/platform/crm/sync', {credentials:'same-origin'});
      const data = await response.json();
      if (epoch !== generation) return;
      if (!response.ok || !data.success) throw new Error('status unavailable');
      let active = false;
      const completed = [];
      for (const job of data.jobs || []) {
        if (job.status === 'queued' || job.status === 'running') {
          active = true;
          display(job.resource, `Kommo fonda yenilənir… ${job.pages_done || 0} səhifə yükləndi. Saxlanmış siyahı göstərilir.`);
        } else if (job.status === 'failed') {
          display(job.resource, 'Sinxronizasiya dayandı. Kommo bağlantısını yoxlayın və Yenilə düyməsini basın.');
        } else if (job.status === 'done') {
          display(job.resource, 'Kommo məlumatları yeniləndi.');
          completed.push(job.resource);
        }
      }
      watching = active;
      if (!active && completed.length) document.dispatchEvent(new CustomEvent('tenant-crm-sync-complete', {
        detail: {resources:completed, tenant_id:profile.tenant_id}
      }));
    } catch (_) {
      if (epoch !== generation) return;
      watching = false;
      for (const resource of Object.keys(boxes)) display(resource, 'Sinxronizasiya vəziyyəti alınmadı. Yenilə düyməsi ilə yoxlayın.');
    }
    schedule(epoch);
  }
  function start() {
    if (!profile) return;
    generation++; watching = true; clearTimeout(timer); poll(generation);
  }
  window.tenantCrmSyncWatch = start;
  document.addEventListener('tenant-profile', event => {
    generation++; clearTimeout(timer); watching = false;
    profile = event.detail.member;
    for (const resource of Object.keys(boxes)) display(resource, '');
    const modules = event.detail.capabilities?.modules || {};
    if (modules.tasks || modules.deals || modules.customers) start();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(timer); return; }
    if (watching) { generation++; poll(generation); }
  });
})();
