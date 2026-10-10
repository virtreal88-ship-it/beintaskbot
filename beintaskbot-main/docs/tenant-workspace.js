/* Presentation only. CRM access and workflow decisions remain server-owned. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function columns(rows, catalog = []) {
    const pipelines = new Map(catalog.map(p => [String(p.pipeline_id), {...p, stages: (p.stages || []).map(s => ({...s, rows: []}))}]));
    for (const row of rows) {
      const pid = String(row.pipeline_id || 0), sid = String(row.status_id || 0);
      if (!pipelines.has(pid)) pipelines.set(pid, {pipeline_id: pid, name: '', stages: []});
      const pipeline = pipelines.get(pid);
      let stage = pipeline.stages.find(s => String(s.stage_id) === sid);
      if (!stage) { stage = {stage_id: sid, name: row.stage_name || 'Mərhələ göstərilməyib', rows: []}; pipeline.stages.push(stage); }
      stage.rows.push(row);
    }
    for (const pipeline of pipelines.values()) for (const stage of pipeline.stages)
      stage.rows.sort((a,b) => (Date.parse(b.source_updated_at || b.synced_at) || 0) - (Date.parse(a.source_updated_at || a.synced_at) || 0));
    return [...pipelines.values()];
  }
  function date(value) {
    if (!value) return '—';
    const d = new Date(value); if (Number.isNaN(d.getTime())) return '—';
    const pad = v => String(v).padStart(2,'0');
    return `${pad(d.getDate())}/${pad(d.getMonth()+1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  if (typeof module !== 'undefined' && module.exports) { module.exports = {columns, date, escape}; return; }
  const $ = id => document.getElementById(id);
  let catalog = [], mode = 'board', pipelineFilter = '';
  const css = document.createElement('style');
  css.textContent = `
    :root{--paper:#f4f6f8;--line:#e5e9ed;--ink:#172b32;--muted:#73818a}
    aside{background:#132c2c;padding:26px 16px}.brand{font-size:17px;letter-spacing:-.4px}.mark{display:none}.company{margin:8px 12px;color:#9fb5b5}.nav{flex:1;display:flex;flex-direction:column;gap:6px}.nav .settings{margin-top:auto}.nav button{padding:12px;gap:12px;font-size:14px}.nav svg{width:19px;height:19px;flex:none}.nav button.active{background:#25504b}
    main{padding:30px 28px 64px}.top{margin-bottom:22px}.top h1{font-size:27px;letter-spacing:-.7px;font-weight:650}.eyebrow{display:none}.top p{font-size:13px;margin-top:6px}.toolbar{margin:0 0 20px;gap:10px}.refresh,.primary{border-radius:8px;padding:10px 14px;font-size:14px}.table{border-radius:12px}.row{width:100%;background:white;text-align:left}.panel{border-radius:12px;padding:22px}.panel h2{font-weight:650}.panel p{font-size:13px;line-height:1.6}button:focus-visible,a:focus-visible,select:focus-visible{outline:3px solid #7bc7b5;outline-offset:2px}
    .workspace-controls{display:flex;gap:8px;align-items:center}.workspace-controls select{max-width:220px;border:1px solid var(--line);background:white;border-radius:8px;padding:10px;color:var(--ink)}.workspace-modes{display:flex;background:#e9eef0;padding:3px;border-radius:9px}.workspace-modes button{padding:7px 11px;border-radius:7px;background:transparent;color:var(--muted);font-size:13px}.workspace-modes button[aria-pressed=true]{background:white;color:var(--ink);box-shadow:0 1px 3px #20343a15}
    #dealsList.workspace-board{background:none;border:0;overflow:visible}.workspace-pipeline{margin:0 0 28px}.workspace-pipeline h2{font-size:15px;font-weight:650;margin:0 0 12px}.workspace-lanes{display:flex;gap:14px;overflow-x:auto;padding:0 0 12px;overscroll-behavior-x:contain}.workspace-lane{flex:0 0 276px;background:#edf1f3;border:1px solid #e4e9ec;border-radius:12px;padding:10px;min-height:170px}.workspace-lane-header{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:5px 3px 13px;font-size:13px;font-weight:650}.workspace-lane-header span{background:#dce5e7;color:#61747a;border-radius:6px;padding:2px 7px;font-size:12px}.workspace-card{display:block;width:100%;background:white;text-align:left;border:1px solid #e1e7ea;border-radius:10px;padding:15px;margin-bottom:10px;box-shadow:0 2px 4px #20343a04;transition:border-color .15s}.workspace-card:hover{border-color:#8ebbb0}.workspace-card strong{display:block;font-size:14px;overflow-wrap:anywhere;line-height:1.5}.workspace-phone{display:block;font-size:13px;color:#2b7367;margin:4px 0 10px}.workspace-card .sub{white-space:normal;overflow-wrap:anywhere;font-size:12px}.workspace-card-footer{display:flex;justify-content:space-between;gap:8px;margin-top:13px;color:#88969b;font-size:11px}.workspace-empty{padding:28px 8px;text-align:center;color:#88969b;font-size:12px}
    .settings-sections{display:flex;gap:5px;flex-wrap:wrap;border-bottom:1px solid var(--line);margin:0 0 22px;padding-bottom:12px}.settings-sections button{padding:9px 14px;border-radius:8px;background:transparent;color:var(--muted);font-size:14px}.settings-sections button[aria-selected=true]{background:#e3f1ec;color:#146954;font-weight:650}#view-settings [data-settings-section][hidden]{display:none!important}#view-settings>.panel{margin-bottom:16px}#view-settings>.grid{align-items:start}.settings-help{font-size:13px;color:var(--muted);margin:-7px 0 20px}
    @media(max-width:760px){aside{padding:10px 12px;gap:10px}.brand{font-size:14px}.nav{flex-direction:row;flex:0 1 auto}.nav button{font-size:0;padding:10px}.nav svg{width:20px;height:20px}.nav .settings{margin-top:0}main{padding:22px 16px 84px}.top h1{font-size:23px}.toolbar{gap:10px}.workspace-controls{width:100%;justify-content:space-between}.workspace-controls select{min-width:0;max-width:55%;flex:1}.workspace-lane{flex-basis:calc(100vw - 48px);max-width:340px}.settings-sections{flex-wrap:nowrap;overflow-x:auto}.settings-sections button{white-space:nowrap}.panel{padding:18px}.workspace-card{padding:16px}.top p{display:block}.top a{font-size:12px;padding:8px}.search{min-width:0;flex-basis:100%}}
  `;
  document.head.append(css);
  css.textContent += `.top{justify-content:flex-start}.top>div:first-child{margin-right:auto}#view-settings>.grid>.panel[data-settings-section=integrations]{grid-column:1/-1}@media(max-width:760px){.top{flex-wrap:wrap}.top>div:first-child{flex-basis:100%}}`;
  const paths = {tasks:'M9 5h10v15H5V5h4m0-2h6v4H9zM8 11h8M8 15h5',customers:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8m8-7a4 4 0 0 1 0 8m5 9v-2a4 4 0 0 0-3-4',deals:'M3 4h18v16H3zM9 4v16M15 4v16',settings:'M4 7h16M4 17h16M8 4v6M16 14v6',profile:'M20 21a8 8 0 0 0-16 0M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10',hot_orders:'M13 3c1 6-6 7-6 12a5 5 0 0 0 10 0c0-3-1-4-2-5 0 3-2 3-2 3',approvals:'M5 12l4 4L19 6',finance:'M12 3v18M17 7H9a3 3 0 0 0 0 6h6a3 3 0 0 1 0 6H6'};
  paths.linear='M4 4h16v16H4zM8 8h3v3H8zM14 8h2M8 15h8';
  paths.news='M4 4h16v16H4zM8 8h8M8 12h8M8 16h5';
  function updateIcons() { document.querySelectorAll('.nav [data-view]').forEach(button => {
    const span = button.querySelector('span'), path = paths[button.dataset.view];
    if (span && path) span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;
  }); }
  updateIcons();
  const toolbar = document.querySelector('#view-deals .toolbar');
  toolbar.insertAdjacentHTML('beforeend', `<div class="workspace-controls"><select id="workspacePipeline" aria-label="Vərəq"><option value="">Bütün vərəqlər</option></select><div class="workspace-modes" aria-label="Görünüş"><button type="button" data-workspace-mode="board" aria-pressed="true">Kanban</button><button type="button" data-workspace-mode="list" aria-pressed="false">Siyahı</button></div></div>`);
  function refresh() { document.dispatchEvent(new CustomEvent('tenant-deals-view-change')); }
  $('workspacePipeline').onchange = e => {pipelineFilter = e.target.value;refresh();};
  toolbar.querySelectorAll('[data-workspace-mode]').forEach(button => button.onclick = () => {
    mode = button.dataset.workspaceMode;
    toolbar.querySelectorAll('[data-workspace-mode]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    refresh();
  });
  window.tenantDealBoard = {
    render(target, rows, openDeal) {
      target.classList.toggle('workspace-board', mode === 'board');
      const all = columns(rows,catalog);
      const options = all.map(p => `<option value="${escape(p.pipeline_id)}">${escape(p.name || 'Vərəq #'+p.pipeline_id)}</option>`).join('');
      $('workspacePipeline').innerHTML = '<option value="">Bütün vərəqlər</option>'+options;
      if (!all.some(p => String(p.pipeline_id) === pipelineFilter)) pipelineFilter = '';
      $('workspacePipeline').value = pipelineFilter;
      if (mode === 'list') return pipelineFilter ? rows.filter(r => String(r.pipeline_id) === pipelineFilter) : rows;
      target.innerHTML = all.filter(p => !pipelineFilter || String(p.pipeline_id) === pipelineFilter).map(p => `<section class="workspace-pipeline"><h2>${escape(p.name || 'Vərəq #'+p.pipeline_id)}</h2><div class="workspace-lanes">${p.stages.map(s => `<div class="workspace-lane"><div class="workspace-lane-header">${escape(s.name || 'Mərhələ #'+s.stage_id)}<span>${s.rows.length}</span></div>${s.rows.map(r => `<button type="button" class="workspace-card" data-deal="${escape(r.kommo_lead_id)}"><strong>${escape(r.contact_name || r.name || 'Adsız sövdələşmə')}</strong><span class="workspace-phone">${escape(r.phone || 'Telefon göstərilməyib')}</span><div class="sub">${escape(r.name || '')}</div><div class="workspace-card-footer"><span>#${escape(r.kommo_lead_id)}</span><time>${escape(date(r.source_updated_at || r.synced_at))}</time></div></button>`).join('') || '<div class="workspace-empty">Bu mərhələdə sövdələşmə yoxdur</div>'}</div>`).join('')}</div></section>`).join('') || '<div class="empty">Bu filtr üzrə sövdələşmə yoxdur.</div>';
      target.querySelectorAll('[data-deal]').forEach(button => button.onclick = () => openDeal(button.dataset.deal));
      return null;
    }
  };
  const settings = $('view-settings');
  const groups = [['integrations','İnteqrasiyalar','Xidmətləri qoşun və bağlantıları yoxlayın.'],['employees','Əməkdaşlar','Dəvətlər, giriş sorğuları və komandanız.'],['workflow','İş qaydaları','Vərəqlər, mərhələlər, əməkdaş hüquqları və təsdiq qaydaları.'],['ai','Süni intellekt','AI bağlantısı və cavab qaydaları.'],['notifications','Bildirişlər','Xəbər kanalı və nəşr bildirişləri.'],['advanced','Diaqnostika','Bağlantı və xidmətlərin vəziyyəti.']];
  let section = 'integrations';
  const sections = document.createElement('div'); sections.className = 'settings-sections'; sections.setAttribute('role','tablist');
  sections.innerHTML = groups.map(([key,label]) => `<button type="button" role="tab" data-settings-tab="${key}">${label}</button>`).join('');
  const help = document.createElement('p'); help.className='settings-help';
  settings.querySelector('.top').after(sections,help);
  function classify(panel) {
    if (panel.id === 'workflowPanel') return 'workflow';
    if (/AiPanel/.test(panel.id)) return 'ai';
    if (panel.id === 'tnChannelPanel') return 'notifications';
    if (panel.id === 'tenantLinearPanel' || panel.querySelector('#integrationInfo')) return 'integrations';
    if (panel.querySelector('#inviteName,#members,#accessRequests')) return 'employees';
    return 'advanced';
  }
  function showSettings() {
    const panels = [...settings.querySelectorAll(':scope > .panel,:scope > .grid > .panel')];
    panels.forEach(panel => {panel.dataset.settingsSection = classify(panel);panel.hidden = panel.dataset.settingsSection !== section;});
    sections.querySelectorAll('button').forEach(button => {
      button.hidden = !panels.some(panel => panel.dataset.settingsSection === button.dataset.settingsTab);
      button.setAttribute('aria-selected',String(button.dataset.settingsTab === section));
    });
    help.textContent = groups.find(g => g[0] === section)?.[2] || '';
  }
  sections.querySelectorAll('button').forEach(button => button.onclick = () => {section=button.dataset.settingsTab;showSettings();});
  const observer = new MutationObserver(showSettings);
  observer.observe(settings,{childList:true}); observer.observe(settings.querySelector('.grid'),{childList:true});
  document.addEventListener('tenant-profile', e => {catalog=e.detail.capabilities?.deal_board || [];updateIcons();showSettings();refresh();});
  showSettings();
})();
