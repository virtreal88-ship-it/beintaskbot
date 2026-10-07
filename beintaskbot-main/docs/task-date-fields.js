/* Fixed display format; native ISO values and existing change handlers stay intact. */
function taskDateDisplay(iso) {
    const match=String(iso||'').match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    return match ? `${match[3]}/${match[2]}/${match[1]} ${match[4]}:${match[5]}` : '';
}
function taskDateIso(display) {
    if(!String(display||'').trim()) return '';
    const match=String(display).trim().match(/^(\d{2})\/(\d{2})\/(\d{4})\s+(\d{2}):(\d{2})$/);
    if(!match) return null;
    const [,day,month,year,hour,minute]=match;
    const date=new Date(`${year}-${month}-${day}T${hour}:${minute}:00Z`);
    if(Number.isNaN(date.getTime()) || date.getUTCDate()!==Number(day) || date.getUTCMonth()+1!==Number(month)
       || date.getUTCFullYear()!==Number(year) || date.getUTCHours()!==Number(hour) || date.getUTCMinutes()!==Number(minute)) return null;
    return `${year}-${month}-${day}T${hour}:${minute}`;
}
function validateTaskDateField(id) {
    const field=document.getElementById(id)?.taskDateText;
    return !field || field.reportValidity();
}
(() => {
    const selector='#task-deadline-custom,#edit-deadline-custom,#modal-task-deadline,#deal-modal-deadline,#sub-deadline';
    function enhance(native) {
        if(native.taskDateText || native.type!=='datetime-local') return;
        const row=document.createElement('div');row.style.cssText='position:relative;display:flex;gap:6px;align-items:center;width:100%;margin-top:8px';
        const text=document.createElement('input');text.type='text';text.className=native.className;
        text.style.cssText='width:100%;min-width:0;flex:1;padding:10px;border:1px solid #cbd5e1;border-radius:10px';
        text.placeholder='DD/MM/YYYY HH:mm';text.setAttribute('aria-label','Tarix və saat: gün/ay/il saat:dəqiqə');
        text.required=native.required;native.taskDateText=text;
        const button=document.createElement('button');button.type='button';button.textContent='▦';
        button.setAttribute('aria-label','Təqvimdən seç');button.style.cssText='padding:9px 12px;border:1px solid #cbd5e1;border-radius:10px;background:#f8fafc';
        native.before(row);row.append(text,button,native);
        native.style.cssText='position:absolute;right:0;bottom:0;width:1px;height:1px;opacity:0;pointer-events:none';
        native.tabIndex=-1;
        const value=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');
        const refresh=()=>{text.value=taskDateDisplay(value.get.call(native));text.setCustomValidity('');};
        Object.defineProperty(native,'value',{configurable:true,get(){return value.get.call(this);},set(next){value.set.call(this,next);refresh();}});
        native.addEventListener('change',refresh);
        text.addEventListener('input',()=>{
            const iso=taskDateIso(text.value);
            text.setCustomValidity(iso===null?'Tarixi DD/MM/YYYY HH:mm formatında yazın.':'');
            // Do not silently reuse an old deadline after invalid manual input.
            value.set.call(native,iso===null?'':iso);
            native.dispatchEvent(new Event('input',{bubbles:true}));
            if(iso!==null) native.dispatchEvent(new Event('change',{bubbles:true}));
        });
        button.onclick=()=>{
            if(typeof native.showPicker==='function') { try { native.showPicker();return; } catch(_) {} }
            text.focus();
        };
        refresh();
    }
    function scan(root) {
        if(root.matches?.(selector)) enhance(root);
        root.querySelectorAll?.(selector).forEach(enhance);
    }
    scan(document);
    new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(node=>{if(node.nodeType===1)scan(node);})))
        .observe(document.body,{childList:true,subtree:true});
})();
