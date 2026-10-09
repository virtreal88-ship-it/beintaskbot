const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(){
  const events={},calls=[],opened=[];let modal,response;
  const input={value:'',focus(){}},button={setAttribute(){},disabled:false},status={textContent:''},form={},close={};
  const results={textContent:'',innerHTML:'',querySelectorAll:()=>[]};
  const nodes={'input':input,'[type=submit]':button,'[role=status]':status,'[data-results]':results,'[data-close]':close,'form':form};
  const env={CURRENT_USER_ID:20,URLSearchParams,AbortController,setTimeout,clearTimeout,location:{pathname:'/webapp'},
    openDealViewById:id=>opened.push(id),document:{head:{insertAdjacentHTML(){}},body:{append(node){modal=node;node.isConnected=true;}},
      addEventListener:(name,fn)=>events[name]=fn,createElement:()=>({querySelector:selector=>nodes[selector],remove(){this.isConnected=false;}})},
    fetch:async(url,opt)=>{calls.push({url,opt});return response();}};
  response=async()=>({ok:true,json:async()=>({success:true,phone:'+994551234567',deals:[{id:10,contact_name:'<script>evil</script>',name:'Order',phone:'0551234567',pipeline_name:'Sales',stage_name:'New'}]})});
  vm.runInNewContext(fs.readFileSync('docs/deal-phone-duplicates.js','utf8'),env);
  const open=()=>events.click({target:{closest:()=>({dataset:{duplicateSearch:'7',duplicatePhone:'0551234567'}})}});
  return {events,calls,input,button,status,results,form,close,open,setResponse:fn=>response=fn};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  let f=fixture();f.open();assert.equal(f.calls.length,0);assert.equal(f.input.value,'0551234567');
  await f.form.onsubmit({preventDefault(){}});assert.equal(f.calls.length,1);
  assert.equal(f.calls[0].opt.headers['X-TG-User-ID'],'20');assert.ok(f.calls[0].url.includes('phone=0551234567'));
  assert.ok(f.results.innerHTML.includes('&lt;script&gt;'));assert.ok(!f.results.innerHTML.includes('<script>'));
  assert.equal(f.calls[0].opt.method,undefined);assert.equal(f.button.disabled,false);
  f=fixture();f.open();let resolve;f.setResponse(()=>new Promise(r=>resolve=r));const late=f.form.onsubmit({preventDefault(){}});await flush();
  await f.form.onsubmit({preventDefault(){}});assert.equal(f.calls.length,1);f.close.onclick();
  resolve({ok:true,json:async()=>({success:true,deals:[{id:99}]})});await late;assert.equal(f.results.innerHTML,'');
  assert.equal(f.calls[0].opt.signal.aborted,true);
  f=fixture();f.open();f.setResponse(async()=>({ok:false,json:async()=>({success:false,error:'Telefon nömrəsini tam yazın.'})}));
  await f.form.onsubmit({preventDefault(){}});assert.ok(f.status.textContent.includes('tam yazın'));assert.equal(f.button.disabled,false);
  console.log('PASS: manual phone search, prefilled phone, read-only request, safe results, double click and late response protection.');
})().catch(error=>{console.error(error);process.exitCode=1;});
