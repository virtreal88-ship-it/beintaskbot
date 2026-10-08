/* Owner's declarative workflow editor. No provider mutations. */
(() => {
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const option=(row,selected)=>`<option value="${esc(row.id)}" ${selected.includes(row.id)?'selected':''}>${esc(row.name)}</option>`;
  let states=[];
  function render(config,team){
    states=team?.states?.nodes||[];
    const rules=config.workflow||{};
    document.getElementById('tlRules').innerHTML=`<h3>Linear iş qaydaları</h3>
      <label style="display:flex"><input id="tlEnabled" type="checkbox" style="width:auto" ${rules.enabled?'checked':''}>Kabinetdə Linear tapşırıqlarını aktiv et</label>
      <label>Görünüş şərti<select id="tlVisibility"><option value="any">Account / Operator / İcraçı — ən azı biri</option><option value="all">Təyin edilmiş bütün şərtlər uyğun olmalıdır</option></select></label>
      <label>Account sətrinin başlığı<input id="tlAccountPrefix" value="${esc(rules.account_prefix||'Account')}"></label>
      <label>Operator sətrinin başlığı<input id="tlOperatorPrefix" value="${esc(rules.operator_prefix||'Operator')}"></label>
      <p>Mövcud açıqlamadakı «Account: …» və «Operator: …» sətirləri istifadə olunur. Linear-da yeni xüsusi sahələr yaradılmır.</p>
      <label>Yeni tapşırığın statusu<select id="tlCreationState"><option value="">Yaratma bağlıdır</option>${states.map(row=>option(row,[rules.creation_state_id])).join('')}</select></label>
      <label>Məcburi sahələr (başlıq həmişə məcburidir)<select id="tlRequired" multiple style="min-height:100px">${[{id:'account',name:'Account'},{id:'operator',name:'Operator'},{id:'project',name:'Layihə'},{id:'body',name:'Açıqlama'}].map(row=>option(row,rules.required_fields||['account','operator','project','body'])).join('')}</select></label>
      <h3>Keçid düymələri</h3><div id="tlButtons"></div><button type="button" class="outline" id="tlAddButton">+ Keçid düyməsi</button>`;
    document.getElementById('tlVisibility').value=rules.visibility_mode||'any';
    (rules.buttons||[]).forEach(add);
    document.getElementById('tlAddButton').onclick=()=>{add({id:'action-'+crypto.randomUUID().slice(0,8)});sync();};
    document.getElementById('tlButtons').addEventListener('input',sync);
    document.querySelectorAll('#tlMembers [data-member]').forEach(row=>{
      const binding=config.members?.[row.dataset.member]||{};
      row.querySelector('[data-field="assignee_id"]').innerHTML='<option value="">Təyin edilməyib</option>'+(team?.members?.nodes||[]).map(person=>option(person,[binding.assignee_id])).join('');
      row.querySelector('[data-field="button_ids"]').dataset.selected=JSON.stringify(binding.button_ids||[]);
    });sync();
  }
  function add(button){
    const container=document.getElementById('tlButtons');
    container.insertAdjacentHTML('beforeend',`<div data-button="${esc(button.id)}" style="border:1px solid var(--line);border-radius:12px;padding:12px;margin:12px 0">
      <label>Düymənin adı<input data-button-field="label" value="${esc(button.label||'')}"></label>
      <label>Hansı statuslarda göstərilsin<select data-button-field="from_state_ids" multiple style="min-height:100px">${states.map(row=>option(row,button.from_state_ids||[])).join('')}</select></label>
      <label>Hansı statusa keçsin<select data-button-field="to_state_id"><option value="">Seçin</option>${states.map(row=>option(row,[button.to_state_id])).join('')}</select></label>
      <label style="display:flex"><input style="width:auto" type="checkbox" data-button-field="require_reason" ${button.require_reason?'checked':''}>Səbəb məcburidir (Linear şərhi)</label>
      <button class="outline danger" type="button" data-remove-button>Sil</button></div>`);
    container.lastElementChild.querySelector('[data-remove-button]').onclick=()=>{container.querySelector(`[data-button="${CSS.escape(button.id)}"]`)?.remove();sync();};
  }
  function sync(){
    const buttons=[...document.querySelectorAll('#tlButtons [data-button]')].map(row=>({id:row.dataset.button,label:row.querySelector('[data-button-field="label"]').value||'Yeni düymə'}));
    document.querySelectorAll('#tlMembers [data-field="button_ids"]').forEach(select=>{
      const selected=select.dataset.selected?JSON.parse(select.dataset.selected):[...select.selectedOptions].map(o=>o.value);
      delete select.dataset.selected;select.innerHTML=buttons.map(row=>option({id:row.id,name:row.label},selected)).join('');
    });
  }
  function read(){return {enabled:document.getElementById('tlEnabled').checked,visibility_mode:document.getElementById('tlVisibility').value,
    account_prefix:document.getElementById('tlAccountPrefix').value,operator_prefix:document.getElementById('tlOperatorPrefix').value,
    creation_state_id:document.getElementById('tlCreationState').value,
    required_fields:[...document.getElementById('tlRequired').selectedOptions].map(o=>o.value),
    buttons:[...document.querySelectorAll('#tlButtons [data-button]')].map(row=>({id:row.dataset.button,
      ...Object.fromEntries([...row.querySelectorAll('[data-button-field]')].map(input=>[input.dataset.buttonField,
        input.multiple?[...input.selectedOptions].map(o=>o.value):input.type==='checkbox'?input.checked:input.value]))}))};}
  window.TenantLinearRules={render,read};
})();
