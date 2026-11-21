/* Page-local request sharing. Nothing is persisted across authentication/navigation. */
(()=>{
  const nativeFetch=window.fetch.bind(window),pending=new Map();
  let generation=0,scope='',profileResponse=null,profileUntil=0,profileKey='';
  const fingerprint=data=>JSON.stringify([data?.member?.tenant_id,data?.member?.telegram_id,data?.member?.role,data?.member?.active,data?.member?.department_id,data?.member?.permissions,data?.capabilities,data?.role_permissions]);
  function clearReads(){pending.clear();profileResponse=null;profileUntil=0;}
  function reset(){generation++;clearReads();}
  let authPending=false;
  document.addEventListener('tenant-profile',event=>{const next=fingerprint(event.detail);if(scope&&next!==scope)reset();scope=next;authPending=false;});
  function invalidateAuth(){reset();scope='';window.tenantProfileSnapshot=null;document.dispatchEvent(new CustomEvent('tenant-auth-invalid'));}
  window.addEventListener('pagehide',invalidateAuth);
  window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
  // Guard body consumption too: a response can arrive before logout while json()
  // is still pending. Each consumer gets its own response body and epoch check.
  function scopedResponse(response,version){
    const clone=response.clone.bind(response);
    response.clone=()=>scopedResponse(clone(),version);
    for(const method of ['json','text','blob','arrayBuffer','formData']){
      const read=response[method].bind(response);
      response[method]=async()=>{const value=await read();if(version!==generation)throw new Error('Request scope changed');return value;};
    }
    return response;
  }
  let revalidation=null;
  function pendingAuth(){authPending=true;document.dispatchEvent(new CustomEvent('tenant-auth-pending'));}
  function isTenantAuthFailure(response,url){
    return (response.status===401&&response.headers?.get('X-Tenant-Auth-Source')!=='operator')||
      (url.pathname==='/api/platform/me'&&response.status===403);
  }
  function revalidateProfile(){
    if(revalidation)return revalidation;
    pendingAuth();reset();
    revalidation=(async()=>{try{
      if(window.tenantReloadProfile)return await window.tenantReloadProfile();
      const response=await window.fetch('/api/platform/me',{credentials:'same-origin'}),data=await response.json();
      if(!response.ok||!data?.success||!data.member)throw new Error('Authentication required');
      window.tenantProfileSnapshot=data;document.dispatchEvent(new CustomEvent('tenant-profile',{detail:data}));return data;
    }catch(error){invalidateAuth();throw error;}finally{revalidation=null;}})();
    return revalidation;
  }
  let dialogRevalidation=null,lastFocusCheck=null;
  const visibleDialogs=()=>document.getElementById?.('view-dialogs')?.classList.contains('active');
  const canKeepDialog=()=>!authPending&&visibleDialogs()&&!window.tenantOperator?.resolvingCaseLink&&window.tenantProfileSnapshot?.member?.active===true;
  function verifyOnReturn(){
    if(revalidation)return revalidation;
    if(!canKeepDialog())return revalidateProfile();
    if(dialogRevalidation)return dialogRevalidation;
    if(lastFocusCheck?.generation===generation&&Date.now()-lastFocusCheck.at<500)return Promise.resolve();
    let version=generation;
    // This read must be fresh, independent of the startup profile cache, and
    // must not publish if logout or a foreground reauthorization won the race.
    dialogRevalidation=(async()=>{
      try{
        const response=await nativeFetch('/api/platform/me',{credentials:'same-origin',cache:'no-store',
          signal:window.AbortSignal?.timeout?.(8000)});
        const data=await response.json();
        if(version!==generation)return;
        if(!response.ok||data?.success!==true||data.member?.active!==true)throw new Error('Authentication required');
        const previous=window.tenantProfileSnapshot;
        if(fingerprint(previous)!==fingerprint(data)){pendingAuth();reset();version=generation;}
        else clearReads();
        if(JSON.stringify(previous)!==JSON.stringify(data)){
          if(window.tenantApplyProfile)window.tenantApplyProfile(data);
          else{
            window.tenantProfileSnapshot=data;
            document.dispatchEvent(new CustomEvent('tenant-profile',{detail:data}));
          }
        }
        if(visibleDialogs()&&!document.hidden)document.dispatchEvent(new CustomEvent('tenant-dialog-reauthorized'));
        lastFocusCheck={at:Date.now(),generation};
      }catch(error){if(version===generation)invalidateAuth();throw error;}
      finally{dialogRevalidation=null;}
    })();
    return dialogRevalidation;
  }
  document.addEventListener('visibilitychange',()=>{
    if(document.hidden){lastFocusCheck=null;if(!canKeepDialog())pendingAuth();}
    else void verifyOnReturn().catch(()=>{});
  });
  window.addEventListener('focus',()=>{if(!document.hidden)void verifyOnReturn().catch(()=>{});});
  window.tenantRequestCoordinator={reset,invalidateAuth,revalidateProfile};
  window.fetch=function(input,options={}){
    const url=new URL(typeof input==='string'?input:input?.url||String(input),location.href);
    const method=String(options.method||input?.method||'GET').toUpperCase();
    if(url.origin!==location.origin||!url.pathname.startsWith('/api/platform/'))return nativeFetch(input,options);
    if(method!=='GET'){if(['/api/platform/logout','/api/platform/workspace/switch'].includes(url.pathname))invalidateAuth();else clearReads();return nativeFetch(input,options).then(response=>{clearReads();if(isTenantAuthFailure(response,url))invalidateAuth();return response;});}
    // Requests with cancellation or custom authorization cannot share another caller's lifetime.
    const headers=new Headers(options.headers||input?.headers||{});
    if(headers.get('content-type')==='application/json')headers.delete('content-type');
    const share=typeof input==='string'&&!options.signal&&!headers.has('authorization')&&options.credentials!=='omit'&&(
      url.pathname==='/api/platform/me'||(/^\/api\/platform\/crm\/(deals|sync)$/.test(url.pathname)&&!url.searchParams.has('refresh')&&!url.searchParams.has('full'))||/^\/api\/platform\/ai\/(dialogs|agents)(\/|$)/.test(url.pathname)||/^\/api\/platform\/crm\/native\/(tasks|deals|contacts|companies)$/.test(url.pathname));
    if(!share)return nativeFetch(input,options).then(response=>{if(isTenantAuthFailure(response,url))invalidateAuth();return response;});
    const isProfile=url.pathname==='/api/platform/me',key=JSON.stringify([url.href,options.credentials||'same-origin',[...headers.entries()].sort(),options.mode||'cors',options.redirect||'follow',options.integrity||'',options.referrer||'',options.referrerPolicy||'']);
    if(isProfile&&profileResponse&&profileKey===key&&Date.now()<profileUntil)return Promise.resolve(scopedResponse(profileResponse.clone(),generation));
    let request=pending.get(key);
    if(!request){
      const version=generation;
      request=nativeFetch(input,{...options,cache:'no-store'}).then(async response=>{
        if(version!==generation)throw new Error('Request scope changed');
        if(isTenantAuthFailure(response,url)){invalidateAuth();return response;}
        // Only the short bootstrap burst reuses a completed profile; later visits reauthorize.
        if(isProfile&&response.ok){
          const data=await response.clone().json().catch(()=>null);
          if(version!==generation)throw new Error('Request scope changed');
          if(data?.success&&data?.member&&pending.get(key)===request){profileResponse=response.clone();profileKey=key;profileUntil=Date.now()+1000;}
        }
        return response;
      }).finally(()=>{if(pending.get(key)===request)pending.delete(key);});
      pending.set(key,request);
    }
    const version=generation;
    return request.then(response=>{if(version!==generation)throw new Error('Request scope changed');return scopedResponse(response.clone(),version);});
  };
})();
