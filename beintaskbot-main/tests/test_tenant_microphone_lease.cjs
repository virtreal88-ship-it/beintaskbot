const {test}=require('node:test');
const assert=require('node:assert/strict');
const acquire=require('../docs/tenant-microphone-lease.js');

function fakeLocks(){
  let held=false;
  return {request:async(name,options,fn)=>{
    assert.equal(name,'beintaskbot-microphone-v1');
    assert.deepEqual(options,{ifAvailable:true,mode:'exclusive'});
    if(held)return fn(null);
    held=true;try{return await fn({name});}finally{held=false;}
  }};
}
test('one shared lease blocks another voice mode until release',async()=>{
  const locks=fakeLocks();
  const release=await acquire(locks);assert.equal(typeof release,'function');
  assert.equal(await acquire(locks),null);
  release();release();await new Promise(resolve=>setImmediate(resolve));
  const next=await acquire(locks);assert.equal(typeof next,'function');next();
});
test('missing or failing lock service fails closed',async()=>{
  await assert.rejects(acquire(null),/microphone_exclusion_unsupported/);
  await assert.rejects(acquire({request:()=>Promise.reject(new Error('lock denied'))}),/lock denied/);
});
