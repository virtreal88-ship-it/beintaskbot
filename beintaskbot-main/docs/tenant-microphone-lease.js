/* Coordinate cooperative voice modes in one browser storage partition. */
((root, make) => {
  if (typeof module === 'object' && module.exports) module.exports = make;
  else root.acquireTenantMicrophone = make;
})(typeof window === 'object' ? window : globalThis, async function acquireTenantMicrophone(locks = globalThis.navigator?.locks) {
  if (!locks || typeof locks.request !== 'function') throw new Error('microphone_exclusion_unsupported');
  return new Promise((resolve, reject) => {
    let settled = false;
    try {
      const pending = locks.request('beintaskbot-microphone-v1', { ifAvailable: true, mode: 'exclusive' }, lock => {
        if (!lock) { settled = true; resolve(null); return; }
        let release;
        const held = new Promise(done => { release = done; });
        settled = true;
        let released = false;
        resolve(() => { if (!released) { released = true; release(); } });
        return held;
      });
      Promise.resolve(pending).catch(error => { if (!settled) { settled = true; reject(error); } });
    } catch (error) { if (!settled) { settled = true; reject(error); } }
  });
});
