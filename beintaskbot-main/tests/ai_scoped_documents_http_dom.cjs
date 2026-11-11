/* Fast contract check for the same real HTML/controller/HTTP acceptance script.
   JSDOM is not Chromium or visual evidence. No API/fetch response mocks. */
const assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('../.support-test-deps/node_modules/jsdom');
const origin=process.env.AI_RUNTIME_ORIGIN;
assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(origin||''),'localhost only');
(async()=>{
 const errors=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));
 const dom=await JSDOM.fromURL(origin+'/app',{runScripts:'dangerously',resources:'usable',pretendToBeVisual:true,virtualConsole:console,
  beforeParse(window){window.fetch=(url,options)=>fetch(new URL(url,window.location.href),options);}});
 try{
  const deadline=Date.now()+20000;
  let result;
  do{await new Promise(resolve=>setTimeout(resolve,20));result=dom.window.document.getElementById('aiAcceptanceResult');}
  while((!result||result.dataset.result==='running')&&Date.now()<deadline);
  assert.equal(result?.dataset.result,'passed',JSON.stringify({result:result?.textContent,errors,host:dom.window.document.getElementById('aiScopedDocuments')?.outerHTML}));
  assert.deepEqual(errors,[]);global.console.log(result.textContent);
 }finally{dom.window.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
