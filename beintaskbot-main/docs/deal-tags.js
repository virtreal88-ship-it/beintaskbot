// Real Kommo lead tags; the editor changes a deal, not the global tag directory.
function dealTagsHtml(record) {
    return (Array.isArray(record?.tags) ? record.tags : []).map(tag =>
        `<span class="kommo-tag"># ${escapeHtml(tag.name || '')}</span>`).join('');
}
function dealTagsFieldHtml(id) {
    return `<div id="${id}" data-tags="[]"><span class="deal-tag-draft"></span>
        <button type="button" class="tag-action" onclick="openDealTagsEditor('${id}',0)">+ Teqlər</button></div>`;
}
function dealTagsDetailsHtml(deal, readonly) {
    return `<div class="deal-tags-row">${dealTagsHtml(deal)}${readonly ? '' :
        `<button type="button" class="tag-action" onclick="openDealTagsEditor('',${Number(deal.id) || 0})">Teqləri dəyiş</button>`}</div>`;
}
async function openDealTagsEditor(draftId, leadId) {
    if(document.getElementById('deal-tags-modal')) return;
    const draft = draftId ? document.getElementById(draftId) : null;
    let tags = draft ? JSON.parse(draft.dataset.tags || '[]') : [];
    let page = 1, epoch = 0, busy = false;
    const modal = document.createElement('div');
    modal.id = 'deal-tags-modal'; modal.className = 'tag-overlay';
    modal.innerHTML = `<section class="tag-dialog" role="dialog" aria-modal="true" aria-label="Kommo teqləri">
        <header><b>Kommo teqləri</b><button type="button" data-close aria-label="Bağla">✕</button></header>
        <p class="tag-help">Teqi dəyişmək yalnız bu sövdələşməyə təsir edir.</p>
        <div data-current class="deal-tags-row"></div>
        <label>Teq axtarın və ya yeni ad yazın<input data-query maxlength="100" placeholder="Teq adı" autocomplete="off"></label>
        <button type="button" data-add class="tag-action">+ Əlavə et</button>
        <div data-error role="status"></div><div data-catalog class="tag-catalog"></div>
        <button type="button" data-more class="tag-action" hidden>Daha çox</button>
    </section>`;
    document.body.appendChild(modal);
    const el = selector => modal.querySelector(selector);
    const query = el('[data-query]');
    const error = el('[data-error]');
    const headers = {'Content-Type': 'application/json', 'X-TG-User-ID': CURRENT_USER_ID};
    el('[data-close]').onclick = () => { if(!busy) { epoch++; modal.remove(); } };
    function paint() {
        el('[data-current]').innerHTML = tags.map((tag, index) => `<span class="kommo-tag">
            ${escapeHtml(tag.name)}<button type="button" data-edit="${index}" aria-label="Teqi dəyiş">✎</button>
            <button type="button" data-remove="${index}" aria-label="Teqi sil">×</button></span>`).join('');
    }
    function sync() {
        if(draft?.isConnected) {
            draft.dataset.tags = JSON.stringify(tags);
            draft.querySelector('.deal-tag-draft').innerHTML = dealTagsHtml({tags});
        }
        if(!leadId) return;
        for(const list of [samilDeals, ...Object.values(samilDealsByStage || {}), typeof adminChatDeals !== 'undefined' ? adminChatDeals : []]) {
            (list || []).forEach(deal => { if(String(deal.id) === String(leadId)) deal.tags = tags; });
        }
        if(_dealViewState && String(_dealViewState.id) === String(leadId)) {
            _dealViewState.deal.tags = tags;
            applyDealSide(_dealViewState.deal, !!_dealViewState.readonly);
        }
        if(leadId) renderSamilDeals();
    }
    async function catalog(append = false, refresh = false) {
        const token = ++epoch;
        error.textContent = 'Yüklənir…';
        try {
            const response = await fetch(API_BASE + '/api/deal/tags?' + new URLSearchParams({
                lead_id: leadId, page, query: query.value.trim()
            }), {headers, cache: 'no-store'});
            const result = await response.json();
            if(token !== epoch || !modal.isConnected) return;
            if(!response.ok || !result.success) throw new Error(result.error || 'Teqlər yüklənmədi');
            if(refresh && leadId) { tags = result.tags; paint(); sync(); }
            if(!append) el('[data-catalog]').replaceChildren();
            result.catalog.forEach(tag => {
                const button = document.createElement('button');
                button.type = 'button'; button.className = 'kommo-tag'; button.textContent = '# ' + tag.name;
                button.onclick = () => change('add', tag);
                el('[data-catalog]').appendChild(button);
            });
            el('[data-more]').hidden = !result.more;
            error.textContent = '';
        } catch(err) { if(token === epoch && modal.isConnected) error.textContent = err.message; }
    }
    async function change(operation, tag, old) {
        if(busy) return;
        busy = true; epoch++;
        modal.querySelectorAll('button,input').forEach(node => node.disabled = true);
        error.textContent = 'Saxlanılır…';
        try {
            if(leadId) {
                const response = await fetch(API_BASE + '/api/deal/tags', {method:'POST', headers,
                    body: JSON.stringify({lead_id:leadId, operation, tag, remove_id:old?.id})});
                const result = await response.json();
                if(!response.ok || !result.success) throw new Error(result.error || 'Teqlər saxlanmadı');
                if(!Array.isArray(result.tags)) {
                    await catalog(false, true);
                    throw new Error('Teq saxlanıldı. Siyahını yenidən açıb yoxlayın.');
                }
                tags = result.tags;
            } else {
                if(old) tags = tags.filter(row => row !== old);
                if(tag && !tags.some(row => tag.id ? row.id === tag.id : row.name === tag.name)) tags.push(tag);
            }
            paint(); sync(); error.textContent = ''; query.value = '';
        } catch(err) { error.textContent = err.message; }
        finally { busy = false; modal.querySelectorAll('button,input').forEach(node => node.disabled = false); }
    }
    el('[data-current]').onclick = event => {
        const button = event.target.closest('button');
        if(!button) return;
        if(button.dataset.remove !== undefined) change('remove', null, tags[Number(button.dataset.remove)]);
        else {
            const old = tags[Number(button.dataset.edit)];
            const name = String(window.prompt('Yeni teq adı (yalnız bu sövdələşmə üçün)', old.name) || '').trim();
            if(name && name !== old.name) change('replace', {name}, old);
        }
    };
    el('[data-add]').onclick = () => { const name = query.value.trim(); if(name) change('add', {name}); };
    let timer;
    query.oninput = () => { epoch++; clearTimeout(timer); timer = setTimeout(() => { if(!busy && modal.isConnected) { page=1; catalog(); } }, 300); };
    el('[data-more]').onclick = () => { if(!busy) { page++; catalog(true); } };
    paint(); await catalog(false, true);
}
const dealTagStyles = document.createElement('style');
dealTagStyles.textContent = `.kommo-tag{display:inline-flex;align-items:center;gap:5px;max-width:100%;overflow-wrap:anywhere;padding:4px 9px;margin:3px;border:1px solid #b2d9d0;border-radius:999px;background:#eaf6f2;color:#176958;font-size:12px;font-weight:600}.kommo-tag button{border:0;background:none;color:inherit;padding:2px 4px}.deal-tags-row{display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin:10px 0}.tag-action{padding:7px 11px;border:1px solid #d5e4df;border-radius:10px;background:white;color:#176958;font-size:12px}.tag-overlay{position:fixed;inset:0;z-index:2147483000;background:#0006;display:flex;align-items:center;justify-content:center;padding:16px}.tag-dialog{background:white;border-radius:20px;padding:20px;width:440px;max-width:100%;max-height:calc(100dvh - 32px);overflow-y:auto;color:#243d36}.tag-dialog header{display:flex;justify-content:space-between;align-items:center}.tag-dialog header button{border:0;background:none;font-size:20px}.tag-help{font-size:12px;color:#64748b;margin:12px 0}.tag-dialog input{width:100%;border:1px solid #d5e4df;border-radius:10px;padding:10px;margin:8px 0}.tag-catalog{margin:12px 0;min-height:35px}.tag-dialog [data-error]{font-size:12px;margin-top:10px;color:#a33}.tag-dialog button:disabled{opacity:.5}`;
document.head.appendChild(dealTagStyles);
