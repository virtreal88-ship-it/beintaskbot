/* Owner-only bounded queue snapshot. No retry/mutation controls. */
(()=>{
  let profile=null, revision=0, controller=null;
  const labels={crm_sync:'CRM sinxronizasiya',task_commands:'Tapşırıq əməliyyatları',deal_commands:'Sövdələşmə əməliyyatları',chat_sends:'Mesaj göndərilməsi',chat_ai:'Çat AI',approval_telegram:'Tapşırıq bildirişləri',crm_notices:'CRM bildirişləri',deal_notices:'Sövdələşmə təsdiqləri',linear_notices:'Linear bildirişləri',news_publications:'Xəbər nəşrləri',hot_order_notices:'İsti sifariş bildirişləri',task_decisions:'Tapşırıq təsdiq nəticələri'};
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  document.addEventListener('tenant-profile',e=>{
    const me=e.detail.member;profile=me;revision++;controller?.abort();
    document.getElementById('tenantDiagnostics')?.remove();
    if(me?.role!=='owner')return;
    const host=document.querySelector('#view-settings .grid');if(!host)return;
    host.insertAdjacentHTML('beforeend','<article class="panel" id="tenantDiagnostics"><h2>Diaqnostika</h2><p>Növbələr və inteqrasiyalar. Yalnız bu şirkətə aiddir.</p><button class="outline" id="diagnosticsRefresh">Yoxla</button><div id="diagnosticsResult" role="status" style="overflow-wrap:anywhere;margin-top:12px"></div></article>');
    document.getElementById('diagnosticsRefresh').onclick=load;
  });
  async function load(){
    const box=document.getElementById('diagnosticsResult'),button=document.getElementById('diagnosticsRefresh');
    if(!box||profile?.role!=='owner')return;
    const ticket=revision,me=profile;controller?.abort();controller=new AbortController();
    const abort=controller,timer=setTimeout(()=>abort.abort(),30000);button.disabled=true;box.textContent='Yüklənir…';
    try{
      const response=await fetch('/api/platform/diagnostics?'+new URLSearchParams({expected_tenant_id:me.tenant_id,expected_user_id:me.telegram_id}),{credentials:'same-origin',signal:abort.signal});
      const data=await response.json();if(ticket!==revision)return;
      if(!response.ok||!data.success)throw new Error((data.error||'Sorğu alınmadı.')+' '+(data.request_id||''));
      box.innerHTML=(data.queues||[]).map(q=>`<div style="padding:8px 0;border-bottom:1px solid #e6eeea"><b>${esc(labels[q.key]||q.key)}</b><div>${!q.available?'Hələ yaradılmayıb':q.states.map(s=>`${esc(s.state)}: ${Number(s.count)}${s.stale?` · gecikmiş: ${Number(s.stale)}`:''}`).join(' · ')||'Boşdur'}</div>${q.limited?'<small>Son 1001 qeyd göstərilir; tam say deyil.</small>':''}</div>`).join('')+
        (data.integrations||[]).map(i=>`<div>${esc(i.provider)}: ${esc(i.status)}</div>`).join('')+'<small>Qeyri-müəyyən əməliyyatlar avtomatik təkrarlanmır.</small>';
    }catch(error){if(ticket===revision)box.textContent=error.name==='AbortError'?'Yükləmə dayandırıldı. Yenidən yoxlayın.':error.message;}
    finally{clearTimeout(timer);button.disabled=false;}
  }
})();
