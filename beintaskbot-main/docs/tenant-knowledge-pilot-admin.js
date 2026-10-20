(()=>{
  const panel=document.getElementById('supportPilotAdmin');
  if(!panel)return;
  const $=id=>document.getElementById(id), form=$('supportPilotForm'), status=$('supportPilotStatus');
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const count=n=>n==null?'əlçatan deyil':esc(n);
  async function load(){
    try{
      const r=await fetch('/api/platform/support-case-pilot',{credentials:'same-origin',cache:'no-store'}), d=await r.json();
      if(r.status===403){panel.hidden=true;return} if(!r.ok||!d.success)throw Error(d.error||'Məlumat əlçatan deyil.');
      panel.hidden=false; form.hidden=false; const x=d.result, s=x.pilot_scope;
      const pf=x.preflight||{status:'not_ready',reasons:['Preflight nəticəsi yoxdur.']};
      const pfLabel=pf.status==='ready_to_configure'?'Sazlamağa hazır':'Hazır deyil';
      const reasons=(pf.reasons||[]).map(v=>`<li>${esc(v)}</li>`).join('');
      status.innerHTML=`Preflight: <b>${pfLabel}</b><ul>${reasons}</ul>Kodda funksiya: ${x.capability==='available_in_code'?'mövcuddur':'təsdiqlənməyib'} · Worker deployment / Railway: <b>təsdiqlənməyib</b> · Qlobal açar: <b>Təsdiqlənməyib</b><br>${esc(x.global_switch_note)}<br>Scope: ${x.scope_verified?'tenant/channel/conversation təsdiqlənib':'təsdiqlənməyib'} · START_AFTER gələcəkdir: ${s?!!x.start_after_future:'xeyr'}<br>Queue: pending ${count(x.queue.pending)}, failed/blocked ${count(x.queue.failed)}, köhnə sərhədöncəsi ${count(x.queue.old_pending)}, uyğun pending ${count(x.queue.eligible_pending)}, mənbəsiz ${count(x.queue.invalid_source)}, işləyən ${count(x.queue.running)}.<br>AI sorğuları: ${count(x.ai_requests)} · Tokenlər: ${count(x.tokens)} · Mövcud ümumi sərhəd: gündə ${count(x.limits?.daily_extraction_max)} çıxarma, gündə ${count(x.limits?.company_daily_ai_max)} AI çağırışı, sorğu başına ${count(x.limits?.max_tokens_per_request)} token. Pilot limiti tətbiq edilmir · Xərc limiti/ölçümü əlçatan deyil.<br>Manual təsdiq tələb olunur · Avtomatik dərc yoxdur · Başlatma bağlıdır · Saxlama AI çağırışı və queue emalı etmir.`;
      if(s){$('pilotTenant').value=s.tenant_id;$('pilotChannel').value=s.channel_id;$('pilotConversation').value=s.conversation_id;$('pilotStartAfter').value=s.start_after}
    }catch(e){panel.hidden=false;form.hidden=true;status.textContent=e.message||'Pilot məlumatları əlçatan deyil.'}
  }
  form.addEventListener('submit',async e=>{
    e.preventDefault();const body={tenant_id:$('pilotTenant').value.trim(),channel_id:$('pilotChannel').value.trim(),conversation_id:$('pilotConversation').value.trim(),start_after:$('pilotStartAfter').value.trim()};
    status.textContent='Sahə yoxlanılır…';
    try{const r=await fetch('/api/platform/support-case-pilot',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),d=await r.json();if(!r.ok||!d.success)throw Error(d.error||'Saxlama alınmadı.');await load()}
    catch(err){status.textContent=err.message||'Saxlama alınmadı.'}
  });
  load();
})();
