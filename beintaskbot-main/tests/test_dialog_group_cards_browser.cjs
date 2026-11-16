/* Synthetic source selection in Chromium; no production requests or messages. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const docs=path.resolve(__dirname,'../docs');
const html=fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
 try{for(const width of [360,1440]){
  const page=await browser.newPage({viewport:{width,height:900}});
  await page.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
  await page.goto('https://example.test/app?view=dialogs');
  await page.evaluate(()=>{
   document.querySelectorAll('.view').forEach(n=>n.classList.toggle('active',n.id==='view-dialogs'));
   const member={tenant_id:'synthetic',telegram_id:11,role:'owner',active:true};
   window.tenantProfileSnapshot={member,capabilities:{modules:{dialogs:true,support:true}}};
   const source=(name,id)=>({provider:'connect',channel_id:id,channel_name:name,lead_id:id==='a'?41:42,conversation_id:'120363432608782851@g.us',customer_name:'Synthetic group',customer_kind:'group',can_send:false,lifecycle_enabled:true,lifecycle:{state:'waiting',revision:0,can_accept:true}});
   const sources=[source('Huseyn','a'),source('Texniki Dəstək','b')];window.historyReads=[];
   window.fetch=async target=>{const url=String(target);let result={};
    if(url.includes('/api/platform/me'))return{ok:true,json:async()=>window.tenantProfileSnapshot};
    if(url.includes('/dialogs/history')){const channel=new URL(url,'https://example.test').searchParams.get('channel_id');window.historyReads.push(channel);result={dialog:sources.find(s=>s.channel_id===channel),messages:[],next_before:null};}
    else if(url.includes('/dialogs?'))result={dialogs:[{...sources[0],group_sources:sources}],channels:[],agents:[],total_count:1,next_offset:null};
    else if(!url.includes('/dialogs/read'))throw Error('Unexpected request '+url);
    return{ok:true,status:200,json:async()=>({success:true,result})};};
  });
  await page.addScriptTag({path:path.join(docs,'tenant-operator-i18n.js')});
  await page.addScriptTag({path:path.join(docs,'tenant-operator-dialogs.js')});
  await page.evaluate(()=>window.tenantOperator.refresh());
  await page.locator('[data-group-source="1"]').waitFor();
  assert.equal(await page.locator('.operator-row').count(),1);
  for(const index of [0,1]){
   const button=page.locator(`[data-group-source="${index}"]`);
   const geometry=await button.evaluate(el=>{const r=el.getBoundingClientRect(),p=el.closest('.operator-row').getBoundingClientRect();return{left:r.left,right:r.right,parentLeft:p.left,parentRight:p.right};});
   assert(geometry.left>=geometry.parentLeft&&geometry.right<=geometry.parentRight+1,JSON.stringify({width,index,geometry}));
   await button.click();
   await page.waitForFunction(channel=>window.historyReads.at(-1)===channel,index===0?'a':'b');
   if(width<900){await page.locator('[data-operator-back]').click();await page.waitForFunction(()=>document.querySelector('#operatorWorkspace').dataset.selected==='false');}
  }
  await page.close();
 }
 console.log('PASS Chromium group card, both source routes and visible controls at 360/1440px');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
