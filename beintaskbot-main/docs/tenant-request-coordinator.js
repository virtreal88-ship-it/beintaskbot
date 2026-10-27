/* Page-local request sharing. Nothing is persisted across authentication/navigation. */
(()=>{
  const nativeFetch=window.fetch.bind(window),pending=new Map();
  let generation=0,scope='',profileResponse=null,profileUntil=0,profileKey='';
  const fingerprint=data=>JSON.stringify([data?.member?.tenant_id,data?.member?.telegram_id,data?.member?.role,data?.member?.active,data?.member?.permissions,data?.capabilities,data?.role_permissions]);
  function clearReads(){pending.clear();profileResponse=null;profileUntil=0;}
  function reset(){generation++;clearReads();}
  document.addEventListener('tenant-profile',event=>{const next=fingerprint(event.detail);if(scope&&next!==scope)reset();scope=next;});
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
  function pendingAuth(){document.dispatchEvent(new CustomEvent('tenant-auth-pending'));}
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
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pendingAuth();else void revalidateProfile().catch(()=>{});});
  window.addEventListener('focus',()=>{if(!document.hidden)void revalidateProfile().catch(()=>{});});
  window.tenantRequestCoordinator={reset,invalidateAuth,revalidateProfile};
  window.fetch=function(input,options={}){
    const url=new URL(typeof input==='string'?input:input?.url||String(input),location.href);
    const method=String(options.method||input?.method||'GET').toUpperCase();
    if(url.origin!==location.origin||!url.pathname.startsWith('/api/platform/'))return nativeFetch(input,options);
    if(method!=='GET'){if(['/api/platform/logout','/api/platform/workspace/switch'].includes(url.pathname))invalidateAuth();else clearReads();return nativeFetch(input,options).then(response=>{clearReads();if(response.status===401||(url.pathname==='/api/platform/me'&&response.status===403))invalidateAuth();return response;});}
    // Requests with cancellation or custom authorization cannot share another caller's lifetime.
    const headers=new Headers(options.headers||input?.headers||{});
    if(headers.get('content-type')==='application/json')headers.delete('content-type');
    const share=typeof input==='string'&&!options.signal&&!headers.has('authorization')&&options.credentials!=='omit'&&(
      url.pathname==='/api/platform/me'||/^\/api\/platform\/ai\/(dialogs|agents)(\/|$)/.test(url.pathname)||/^\/api\/platform\/crm\/native\/(tasks|deals|contacts|companies)$/.test(url.pathname));
    if(!share)return nativeFetch(input,options).then(response=>{if(response.status===401||(url.pathname==='/api/platform/me'&&response.status===403))invalidateAuth();return response;});
    const isProfile=url.pathname==='/api/platform/me',key=JSON.stringify([url.href,options.credentials||'same-origin',[...headers.entries()].sort(),options.mode||'cors',options.redirect||'follow',options.integrity||'',options.referrer||'',options.referrerPolicy||'']);
    if(isProfile&&profileResponse&&profileKey===key&&Date.now()<profileUntil)return Promise.resolve(scopedResponse(profileResponse.clone(),generation));
    let request=pending.get(key);
    if(!request){
      const version=generation;
      request=nativeFetch(input,{...options,cache:'no-store'}).then(async response=>{
        if(version!==generation)throw new Error('Request scope changed');
        if(response.status===401||(url.pathname==='/api/platform/me'&&response.status===403)){invalidateAuth();return response;}
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
