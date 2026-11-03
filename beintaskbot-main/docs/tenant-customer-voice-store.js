/* Local drafts and exact send IDs; no AI audio is stored here. */
((root, make) => {
  if (typeof module === 'object' && module.exports) module.exports = make;
  else root.createTenantVoiceStore = make;
})(typeof window === 'object' ? window : globalThis, function createTenantVoiceStore(indexedDB = globalThis.indexedDB) {
  let opened;
  function db() {
    if (!indexedDB) return Promise.reject(new Error('voice_storage_unavailable'));
    if (!opened) opened = new Promise((resolve, reject) => {
      const request = indexedDB.open('beintaskbot-customer-voice', 3);
      request.onupgradeneeded = () => {
        const upgrade=request.transaction;
        if (!request.result.objectStoreNames.contains('drafts')) request.result.createObjectStore('drafts', {keyPath:'key'});
        if (!request.result.objectStoreNames.contains('receipts')) {
          const receipts=request.result.createObjectStore('receipts',{keyPath:'receiptKey'});receipts.createIndex('byKey','key');
          const drafts=upgrade.objectStore('drafts'),cursor=drafts.openCursor();
          cursor.onsuccess=()=>{const row=cursor.result;if(!row)return;const value=row.value;
            if(value.state==='unknown'||value.state==='sending'){
              receipts.put({...value,receiptKey:JSON.stringify([value.key,value.request_id]),state:'unknown',blob:null});row.delete();
            }
            row.continue();
          };
        } else {
          const receipts=upgrade.objectStore('receipts');if(!receipts.indexNames.contains('byKey'))receipts.createIndex('byKey','key');
        }
        if (!request.result.objectStoreNames.contains('epochs')) request.result.createObjectStore('epochs');
      };
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
  async function receiptChange(key,id,transform) {
    const database=await db();return new Promise((resolve,reject)=>{
      let changed=false;const tx=database.transaction(['drafts','receipts'],'readwrite'),drafts=tx.objectStore('drafts'),receipts=tx.objectStore('receipts');
      const request=drafts.get(key);request.onsuccess=()=>{
        const old=request.result;if(!old||old.request_id!==id)return;
        const next=transform(old);if(next===undefined)return;
        if(next===null)drafts.delete(key);else drafts.put(next);
        if(next===null||next.state==='unknown'||next.state==='accepted'){
          const state=next?.state||'unknown';receipts.put({...old,...(next||{}),receiptKey:JSON.stringify([key,id]),key,request_id:id,state,blob:null});
          if(next&&next!==null)drafts.delete(key);
        }
        changed=true;
      };
      tx.oncomplete=()=>resolve(changed);tx.onabort=tx.onerror=()=>reject(new Error('voice_storage_unavailable'));
    });
  }
  async function purgeUser(tenant,user) {
    const database=await db();
    return new Promise((resolve,reject)=>{
      const tx=database.transaction(['drafts','receipts','epochs'],'readwrite'),cursor=tx.objectStore('drafts').openCursor();
      const epochs=tx.objectStore('epochs');
      const key=JSON.stringify([tenant,user]),get=epochs.get(key),active=epochs.get('active-owner');
      get.onsuccess=()=>epochs.put((get.result||0)+1,key);
      active.onsuccess=()=>{if(active.result===key)epochs.delete('active-owner');const version=epochs.get('owner-version');version.onsuccess=()=>epochs.put((version.result||0)+1,'owner-version');};
      cursor.onsuccess=()=>{
        const row=cursor.result;if(!row)return;
        try{
          const value=row.value,scope=JSON.parse(value.key);
          const matches=Array.isArray(scope)&&scope.length>=2&&scope[0]===tenant&&scope[1]===user;
          if(matches){
            if(value.state==='draft')row.delete();
            else row.update({...value,state:'unknown',blob:null}); // Keep the no-resend receipt identity.
          }
        }catch{/* Ignore unrelated or malformed local entries. */}
        row.continue();
      };
      const receipts=tx.objectStore('receipts').openCursor();receipts.onsuccess=()=>{const row=receipts.result;if(!row)return;
        try{const value=row.value,scope=JSON.parse(value.key);if(Array.isArray(scope)&&scope[0]===tenant&&scope[1]===user)row.delete();}catch{}row.continue();};
      tx.oncomplete=()=>resolve();tx.onabort=tx.onerror=()=>reject(new Error('voice_storage_unavailable'));
    });
  }
  async function ownerVersion() {
    const database=await db();return new Promise((resolve,reject)=>{const tx=database.transaction('epochs','readonly'),req=tx.objectStore('epochs').get('owner-version');tx.oncomplete=()=>resolve(req.result||0);tx.onabort=tx.onerror=()=>reject(new Error('voice_storage_unavailable'));});
  }
  async function activate(tenant,user,expectedVersion) {
    const database=await db();return new Promise((resolve,reject)=>{
      let activated=false;const tx=database.transaction(['drafts','receipts','epochs'],'readwrite'),epochs=tx.objectStore('epochs'),drafts=tx.objectStore('drafts');
      const version=epochs.get('owner-version'),owner=epochs.get('active-owner'),key=JSON.stringify([tenant,user]);
      owner.onsuccess=()=>{
        if((version.result||0)!==expectedVersion)return;
        activated=true;const previous=owner.result;
        if(previous!==key){
          if(previous){const old=epochs.get(previous);old.onsuccess=()=>epochs.put((old.result||0)+1,previous);}
          epochs.put(key,'active-owner');epochs.put(expectedVersion+1,'owner-version');
        }
        const cursor=drafts.openCursor();cursor.onsuccess=()=>{const row=cursor.result;if(!row)return;
          try{const value=row.value,scope=JSON.parse(value.key);if(Array.isArray(scope)&&scope.length>=2&&(scope[0]!==tenant||scope[1]!==user)){
            if(value.state==='draft')row.delete();else row.update({...value,state:'unknown',blob:null});
          }}catch{}row.continue();};
      };
      tx.oncomplete=()=>resolve(activated);tx.onabort=tx.onerror=()=>reject(new Error('voice_storage_unavailable'));
    });
  }
  async function epoch(tenant,user) {
    const database=await db();return new Promise((resolve,reject)=>{const tx=database.transaction('epochs','readonly'),req=tx.objectStore('epochs').get(JSON.stringify([tenant,user]));tx.oncomplete=()=>resolve(req.result||0);tx.onabort=tx.onerror=()=>reject(new Error('voice_storage_unavailable'));});
  }
  async function activeEpoch(tenant,user) {
    const database=await db();return new Promise((resolve,reject)=>{
      const tx=database.transaction('epochs','readonly'),store=tx.objectStore('epochs'),key=JSON.stringify([tenant,user]),owner=store.get('active-owner'),value=store.get(key);
      tx.oncomplete=()=>owner.result===key?resolve(value.result||0):reject(new Error('voice_owner_changed'));
      tx.onabort=tx.onerror=()=>reject(new Error('voice_storage_unavailable'));
    });
  }
  async function createScopedDraft(entry,tenant,user,expected) {
    const database=await db();return new Promise((resolve,reject)=>{
      let created=false;const tx=database.transaction(['drafts','epochs'],'readwrite'),drafts=tx.objectStore('drafts');
      const epochs=tx.objectStore('epochs'),key=JSON.stringify([tenant,user]),owner=epochs.get('active-owner'),check=epochs.get(key);
      check.onsuccess=()=>{if(owner.result!==key||(check.result||0)!==expected)return;const old=drafts.get(entry.key);old.onsuccess=()=>{if(!old.result){drafts.put(entry);created=true;}};};
      tx.oncomplete=()=>resolve(created);tx.onabort=tx.onerror=()=>reject(new Error('voice_storage_unavailable'));
    });
  }
  async function receiptsFor(key) {
    const database=await db();return new Promise((resolve,reject)=>{const tx=database.transaction('receipts','readonly'),req=tx.objectStore('receipts').index('byKey').getAll(key);
      tx.oncomplete=()=>resolve((req.result||[]).sort((a,b)=>(b.createdAt||0)-(a.createdAt||0)));
      tx.onabort=tx.onerror=()=>reject(new Error('voice_storage_unavailable'));});
  }
  async function latestReceipt(key) { return (await receiptsFor(key))[0]||null; }
  return { get: key => transact('readonly', store => store.get(key)),
    createDraft: entry => change(entry.key, old => old ? undefined : entry),
    epoch,activeEpoch,createScopedDraft,ownerVersion,activate,
    claim: (key, id) => change(key, old => old?.request_id===id && old.state==='draft' ? {...old,state:'sending'} : undefined),
    markUnknown: (key, id) => receiptChange(key,id,old=>old.state==='sending'?{...old,state:'unknown',blob:null}:undefined),
    releaseDraft: (key, id, nextId) => change(key, old => old?.request_id===id && old.state==='sending' ? {...old,request_id:nextId,state:'draft'} : undefined),
    removeDraft: (key, id) => change(key, old => old?.request_id===id && old.state==='draft' ? null : undefined),
    removeAccepted: async (key,id,deliveryState='sent') => {
      const database=await db();return new Promise((resolve,reject)=>{let changed=false;const tx=database.transaction(['drafts','receipts'],'readwrite'),drafts=tx.objectStore('drafts'),receipts=tx.objectStore('receipts'),req=drafts.get(key),receiptKey=JSON.stringify([key,id]),saved=receipts.get(receiptKey);
        req.onsuccess=()=>{const old=req.result;if(old?.request_id===id){receipts.put({...old,receiptKey,key,request_id:id,state:'accepted',deliveryState,blob:null});drafts.delete(key);changed=true;}};
        saved.onsuccess=()=>{if(!req.result&&saved.result){receipts.put({...saved.result,state:'accepted',deliveryState,blob:null});changed=true;}};
        tx.oncomplete=()=>resolve(changed);tx.onabort=tx.onerror=()=>reject(new Error('voice_storage_unavailable'));});
    },
    getReceipt:async(key,id)=>{const all=await receiptsFor(key);return all.find(row=>row.request_id===id)||null;},
    latestReceipt,receiptsFor,
    updateReceipt:async(key,id,values)=>{const database=await db();return new Promise((resolve,reject)=>{let changed=false;const tx=database.transaction('receipts','readwrite'),store=tx.objectStore('receipts'),req=store.get(JSON.stringify([key,id]));req.onsuccess=()=>{if(!req.result)return;store.put({...req.result,...values});changed=true;};tx.oncomplete=()=>resolve(changed);tx.onabort=tx.onerror=()=>reject(new Error('voice_storage_unavailable'));});},
    purgeUser };
});
