/* Local drafts and exact send IDs; no AI audio is stored here. */
((root, make) => {
  if (typeof module === 'object' && module.exports) module.exports = make;
  else root.createTenantVoiceStore = make;
})(typeof window === 'object' ? window : globalThis, function createTenantVoiceStore(indexedDB = globalThis.indexedDB) {
  let opened;
  function db() {
    if (!indexedDB) return Promise.reject(new Error('voice_storage_unavailable'));
    if (!opened) opened = new Promise((resolve, reject) => {
      const request = indexedDB.open('beintaskbot-customer-voice', 1);
      request.onupgradeneeded = () => { if (!request.result.objectStoreNames.contains('drafts')) request.result.createObjectStore('drafts', {keyPath:'key'}); };
      request.onsuccess = () => { request.result.onversionchange = () => { request.result.close(); opened = null; }; resolve(request.result); };
      request.onerror = () => { opened = null; reject(new Error('voice_storage_unavailable')); };
      request.onblocked = () => { opened = null; reject(new Error('voice_storage_unavailable')); };
    });
    return opened;
  }
  async function transact(mode, action) {
    const database = await db();
    return new Promise((resolve, reject) => {
      let value;
      const tx = database.transaction('drafts', mode), request = action(tx.objectStore('drafts'));
      request.onsuccess = () => { value = request.result; };
      tx.oncomplete = () => resolve(value);
      tx.onabort = tx.onerror = () => reject(new Error('voice_storage_unavailable'));
    });
  }
  async function change(key, transform) {
    const database = await db();
    return new Promise((resolve, reject) => {
      let changed = false;
      const tx = database.transaction('drafts', 'readwrite'), store = tx.objectStore('drafts'), request = store.get(key);
      request.onsuccess = () => {
        const next = transform(request.result);
        if (next === undefined) return;
        changed = true;
        if (next === null) store.delete(key); else store.put(next);
      };
      tx.oncomplete = () => resolve(changed);
      tx.onabort = tx.onerror = () => reject(new Error('voice_storage_unavailable'));
    });
  }
  async function purgeUser(tenant,user,others=false) {
    const database=await db();
    return new Promise((resolve,reject)=>{
      const tx=database.transaction('drafts','readwrite'),cursor=tx.objectStore('drafts').openCursor();
      cursor.onsuccess=()=>{
        const row=cursor.result;if(!row)return;
        try{
          const value=row.value,scope=JSON.parse(value.key);
          const matches=Array.isArray(scope)&&scope.length>=2&&(others?(scope[0]!==tenant||scope[1]!==user):(scope[0]===tenant&&scope[1]===user));
          if(matches){
            if(value.state==='draft')row.delete();
            else row.update({...value,state:'unknown',blob:null}); // Keep the no-resend receipt identity.
          }
        }catch{/* Ignore unrelated or malformed local entries. */}
        row.continue();
      };
      tx.oncomplete=()=>resolve();tx.onabort=tx.onerror=()=>reject(new Error('voice_storage_unavailable'));
    });
  }
  return { get: key => transact('readonly', store => store.get(key)),
    createDraft: entry => change(entry.key, old => old ? undefined : entry),
    claim: (key, id) => change(key, old => old?.request_id===id && old.state==='draft' ? {...old,state:'sending'} : undefined),
    markUnknown: (key, id) => change(key, old => old?.request_id===id && old.state==='sending' ? {...old,state:'unknown'} : undefined),
    releaseDraft: (key, id) => change(key, old => old?.request_id===id && old.state==='sending' ? {...old,state:'draft'} : undefined),
    removeDraft: (key, id) => change(key, old => old?.request_id===id && old.state==='draft' ? null : undefined),
    removeAccepted: (key, id) => change(key, old => old?.request_id===id ? null : undefined),purgeUser,
    purgeOtherUsers: (tenant,user) => purgeUser(tenant,user,true) };
});
