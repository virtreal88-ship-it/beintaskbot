/* Settings only: never connects new companies to the shared legacy queue. */
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const roleNames = {owner:'Şirkət sahibi',admin:'Administrator',manager:'Vərəq istifadəçisi',worker:'İcraçı',master:'Usta'};
  const roles = (action, selected) => `<label>${action === 'create' ? 'Yarada bilən rollar' : 'Qəbul edə bilən rollar'}<select multiple data-hot-roles="${action}">${Object.entries(roleNames).map(([id,name])=>`<option value="${id}" ${selected.includes(id) ? 'selected' : ''}>${name}</option>`).join('')}</select></label>`;
  function serviceRow(service) {
    return `<div class="wf-card" data-hot-service="${esc(service.id)}"><label>Xidmətin adı<input data-hot-name maxlength="120" value="${esc(service.name)}" required></label><label class="wf-check"><input type="checkbox" data-hot-active ${service.active !== false ? 'checked' : ''}>Aktivdir</label></div>`;
  }
  function render(settings) {
    return `<h3>İsti sifariş qaydaları</h3><p class="wf-hint">Bildiriş yalnız ümumi mətn və kabinet linkidir. Ödənişlər hələ qoşulmayıb.</p><label class="wf-check"><input type="checkbox" data-hot-completion ${settings.completion_requires_admin!==false ? 'checked' : ''}>Tamamlanma administrator tərəfindən təsdiqlənsin</label><div class="wf-grid">${roles('create',settings.create_roles ?? ['owner','admin'])}${roles('claim',settings.claim_roles ?? ['master'])}</div><div data-hot-services>${(settings.services || []).map(serviceRow).join('')}</div><button type="button" class="outline" data-hot-add>+ Xidmət</button>`;
  }
  function install(root) {
    root.querySelector('[data-hot-add]').onclick = () => {
      const list = root.querySelector('[data-hot-services]');
      if(list.children.length >= 100) return;
      list.insertAdjacentHTML('beforeend',serviceRow({id:crypto.randomUUID(),name:'',active:true}));
      list.lastElementChild.querySelector('[data-hot-name]').focus();
    };
  }
  function read(root, previous) {
    const services = [...root.querySelectorAll('[data-hot-service]')].map(row=>({
      ...(previous.services || []).find(service=>service.id === row.dataset.hotService),
      id:row.dataset.hotService,name:row.querySelector('[data-hot-name]').value.trim(),active:row.querySelector('[data-hot-active]').checked}));
    if(services.some(service=>!service.name)) throw new Error('Xidmətin adını yazın.');
    return {...previous,services,completion_requires_admin:root.querySelector('[data-hot-completion]').checked,...Object.fromEntries(['create','claim'].map(action=>[
      action+'_roles',[...root.querySelector(`[data-hot-roles="${action}"]`).selectedOptions].map(option=>option.value)]))};
  }
  function memberEditor(settings, company) {
    const permission = (action, label) => `<label>${label}<select data-hot-member="${action}"><option value="inherit">Rol qaydası</option><option value="true" ${settings['hot_order_'+action] === true ? 'selected' : ''}>Aktiv</option><option value="false" ${settings['hot_order_'+action] === false ? 'selected' : ''}>Deaktiv</option></select></label>`;
    return `<h3>İsti sifarişlər</h3><div class="wf-grid">${permission('create','Yaratmaq')}${permission('claim','Qəbul etmək')}${permission('completion_requires_admin','Tamamlanma üçün admin təsdiqi')}<label>Xidmətlər<select multiple data-hot-member-services>${(company.services || []).map(service=>`<option value="${esc(service.id)}" ${(settings.hot_order_services || []).includes(service.id) ? 'selected' : ''}>${esc(service.name)}${service.active === false ? ' (deaktiv)' : ''}</option>`).join('')}</select></label></div><p class="wf-hint">Qəbul etmək üçün həm səhifə hüququ, həm aktiv xidmət seçilməlidir. Yeni xidmət əlavə etdikdən sonra saxlayın və ayarları yeniləyin.</p>`;
  }
  function readMember(card, previous) {
    const result={...previous,hot_order_services:[...card.querySelector('[data-hot-member-services]').selectedOptions].map(option=>option.value)};
    for(const action of ['create','claim','completion_requires_admin']) {
      const value=card.querySelector(`[data-hot-member="${action}"]`).value;
      if(value === 'inherit') delete result['hot_order_'+action];
      else result['hot_order_'+action]=value === 'true';
    }
    return result;
  }
  window.tenantHotOrderSettings={render,install,read,memberEditor,readMember};
})();
