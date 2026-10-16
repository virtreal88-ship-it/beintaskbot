/* Integration links stay in /app; permissions are rechecked by every backend. */
(()=>{
 const nav=document.getElementById('nightReportNav');if(!nav)return;
 function profile(data){const allowed=!!data?.capabilities?.modules?.night_report;nav.hidden=!allowed;nav.style.display=allowed?'':'none';}
 function translate(){nav.href='/app/night-report?site_lang='+encodeURIComponent(window.tenantI18n?.language||'ru');const t=window.tenantI18n?.t||((key)=>key);const night=document.getElementById('aiNightTemplateQuick'),status=document.querySelector('[data-ai-reliability-open]');if(night)night.textContent=t('nightOperator');if(status)status.textContent=t('aiStatus');}
 document.addEventListener('tenant-profile',event=>profile(event.detail));document.addEventListener('tenant-language',translate);
 if(window.tenantProfileSnapshot)profile(window.tenantProfileSnapshot);else fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(!window.tenantProfileSnapshot)profile(data)}).catch(()=>{});
 translate();
})();
