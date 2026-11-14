/* Synthetic employee-only console. No real APIs or customer messages. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,'docs/tenant-ai-agents.js'),'utf8');
const code=source.slice(source.indexOf(' function createScopedAgentConsole(){'),source.indexOf(' const scopedAgentConsole=createScopedAgentConsole();'));
assert(code.length>100);
const artifacts=path.join(root,'tests/artifacts/scoped-agents-layout');
const reset='*{box-sizing:border-box;border-width:0;border-style:solid}body{margin:0;background:#f7f9fb;color:#182331;font:16px/1.5 Arial,sans-serif}h1,h2,h3,p{margin:0;font-size:inherit;font-weight:inherit}button,input,select,textarea{font:inherit;color:inherit}button{padding:0;background:transparent;cursor:pointer}main{padding:24px;max-width:1440px;margin:auto}[hidden]{display:none!important}@media(max-width:600px){main{padding:16px}}';
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 try{
  fs.mkdirSync(artifacts,{recursive:true});
  for(const language of ['ru','az','en'])for(const width of [1440,390]){
   const page=await browser.newPage({viewport:{width,height:width===390?844:900}}),errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',route=>route.abort());
   await page.setContent('<!doctype html><html><head><meta charset="utf-8"></head><body><main><section id="view-ai_agents"><article id="legacy">Shared administration</article></section></main></body></html>');
   await page.addStyleTag({content:reset+'\n'+fs.readFileSync(path.join(root,'docs/tenant-ai-dashboard.css'),'utf8')});
   await page.evaluate(({language,code})=>{
    window.tenantI18n={language};window.calls=[];
    window.rows=[{id:'own',name:'Texniki Dəstək · '+('Long descriptive agent title ').repeat(3),instructions:('Verified support instructions.\n').repeat(80)+'x'.repeat(500),enabled:true,mode:'manual',version:1,scopes:[],capabilities:{update:true,delete:true}},{id:'peer',name:'Read-only agent',instructions:'Read-only instructions',enabled:false,mode:'automatic',version:1,scopes:[],capabilities:{update:false,delete:false}}];
    const options={scoped:true,connect_channels:[{id:'source-a',name:'Assigned source'}],stages:[],pipelines:[],models:[{id:'configured',label:'Configured model'}]};
    window.fetch=async(url,request={})=>{window.calls.push({url,method:request.method||'GET'});if(!/^\/api\/platform\/ai\/agents(?:\/options\?cached=1)?$/.test(url))throw Error('Unexpected API '+url);return{ok:true,status:200,json:async()=>({success:true,result:url.includes('options')?options:window.rows})};};
    window.endpoint='/api/platform/ai/';window.agentsVisible=()=>true;window.$=id=>document.getElementById(id);window.esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.eval(code+';window.consoleUI=createScopedAgentConsole();');
    window.consoleUI.profile({member:{tenant_id:'synthetic',telegram_id:31,active:true},capabilities:{modules:{ai_agents:false},pages:{ai_agents:true},ai_documents:{scoped:true,view:true,create:true,update:true,delete:true}}});
   },{language,code});
   const host=page.locator('#aiScopedDocuments');
   await host.locator('[data-scoped-list] article').first().waitFor();
   assert.equal(await page.locator('#legacy').isVisible(),false);
   assert.equal(await host.locator('[data-scoped-list] details').count(),2,'instructions use disclosure');
   assert.equal(await host.locator('[data-scoped-list] details[open]').count(),0,'instructions start collapsed');
   const overflow=()=>page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
   assert.equal(await overflow(),false,language+' '+width+' list overflow');
   const refresh=host.locator('[data-scoped-refresh]');
   const box=await refresh.boundingBox();assert.ok(box.height>=36&&box.width>=50,'refresh target has usable dimensions');
   assert.equal(await host.locator('[data-scoped-list] article').nth(1).locator('button').count(),0,'read-only peer has no mutation buttons');
   await page.screenshot({path:path.join(artifacts,`agents-${language}-${width}.png`),fullPage:true});
   await host.locator('[data-scoped-list] details summary').first().click();
   assert.equal(await host.locator('[data-scoped-list] details').first().getAttribute('open'),'');
   assert.equal(await overflow(),false,'long instructions do not overflow');
   await host.locator('[data-scoped-list] details summary').first().click();
   await host.locator('[data-scoped-list] article button').first().click();
   const form=host.locator('[data-scoped-editor] form');await form.waitFor();
   for(const field of await form.locator('input:not([type=checkbox]),textarea,select').all()){
    const rect=await field.boundingBox();assert.ok(rect&&rect.x>=-1&&rect.x+rect.width<=width+1,'editor field fits viewport');
   }
   assert.equal(await overflow(),false,'editor does not overflow');
   await form.scrollIntoViewIfNeeded();
   await page.screenshot({path:path.join(artifacts,`agents-editor-${language}-${width}.png`),fullPage:true});
   await host.locator('[data-scoped-cancel]').click();assert.equal(await form.count(),0,'cancel closes editor');
   await refresh.click();await page.waitForFunction(()=>window.calls.length>=4);
   assert.equal(await page.evaluate(()=>window.calls.some(c=>c.method!=='GET')),false,'visual checks do not mutate');
   assert.deepEqual(errors,[]);await page.close();
  }
  console.log('PASS scoped AI layout: RU/AZ/EN desktop/mobile, collapsed instructions, bounded editor, permission controls, no mutation; 12 screenshots in tests/artifacts/scoped-agents-layout');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
