const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('../.support-test-deps/node_modules/playwright');
const {appHtml, installFixture} = require('./task_read_save_flow_fixture.cjs');

const scripts = ['tenant-chakra-ui.js','tenant-workspace.js','tenant-contacts-directory.js'];
const html = appHtml().replace('</body>', () => scripts.map(name =>
  '<script>' + fs.readFileSync('docs/'+name,'utf8').replace(/<\/script/gi,'<\\/script') + '</script>'
).join('') + '</body>');

function server({view, source}) {
  const f = window.taskFlowFixture, base = window.fetch;
  const state = window.returnTest = {hold:true, pending:[], status:200, generation:0};
  f.profile.capabilities.modules.customers = true;
  f.profile.capabilities.actions = {customers:{view:true}};
  for (const src of ['native','kommo']) {
    const sample = f[src][0];
    f[src] = Array.from({length:45},(_,i) => {
      const id = src==='native'?'10000000-0000-4000-8000-'+String(i+1).padStart(12,'0'):String(i+1);
      return {...sample,id,kommo_task_id:src==='kommo'?id:undefined,text:'Sample '+src+' '+i};
    });
  }
  const reply = (data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
  window.fetch = async (url, opts={}) => {
    const {pathname,searchParams:q}=new URL(url,location.href);
    if (pathname.endsWith('/task-view-preferences')) return reply({success:true,source});
    const contactRead=pathname.endsWith('/native/contacts') && q.get('listing')==='1';
    const taskRead=pathname.endsWith('/crm/tasks') && q.get('flow')==='1';
    if (!contactRead && !taskRead) return base(url, opts);
    if (state.hold) await new Promise(r=>state.pending.push(r));
    if (state.status!==200) return reply({success:false,error:'test failure'},state.status);
    const selected=q.get('source')||source;
    if (taskRead) {
      if (state.generation) f[selected][0].text='Sample updated';
      return base(url,opts);
    }
    const data=Array.from({length:45},(_,i)=>({id:String(i+1),key:selected+':'+i,
      kind:selected==='native'?'native_contact':'legacy_deal_context',source:selected,
      display_name:i===0&&state.generation?'Sample updated':'Sample '+selected+' '+i,actions:{view:true}}));
    state.contactSearch=q.get('search');
    return reply({success:true,source:selected,data,total:data.length,source_total:data.length,limit:50,offset:0,
      sources:['native','kommo'].map(source=>({source,allowed:true,available:true})),actions:{view:true},state:'ready'});
  };
}

async function scenario(browser, view, source, layout) {
  const page=await browser.newPage({viewport:{width:1350,height:650}});
  await page.addInitScript({content:`(${installFixture})({}); (${server})(${JSON.stringify({view,source})});`});
  await page.route('**/*',r=>r.request().isNavigationRequest()?r.fulfill({contentType:'text/html',body:html}):r.abort());
  page.setDefaultTimeout(9000);
  const isTask=view==='tasks';
  const search=page.locator(isTask?'#taskSearch':'#customerSearch');
  const list=page.locator(isTask?(source==='native'?'#nativeTasksList':'#tasksList'):'#customersList');
  const release=()=>page.evaluate(()=>{returnTest.hold=false;returnTest.pending.splice(0).forEach(resolve=>resolve());});
  try {
    await page.goto('https://fixture.test/app?view='+view+'&lang=ru');
    await page.waitForFunction(()=>returnTest.pending.length>0);
    await page.locator('#view-'+view).getByText('Загрузка…',{exact:true}).first().waitFor({state:'attached'});
    assert(!/Источник недоступен/.test(await page.locator('#view-'+view).innerText()));
    await release();
    if(isTask) {
      await page.waitForFunction(s=>taskFeedSource.value===s&&!taskFeedSource.disabled,source);
      await page.selectOption('#taskStatus','open');
      await page.getByRole('button',{name:layout,exact:true}).click();
    } else {
      await list.getByText('Sample '+source+' 1',{exact:true}).waitFor();
      await page.selectOption('#contactsSource',source);
    }
    await search.fill('Sample');
    const target=list.getByText('Sample '+source+' 30',{exact:true}).first();
    await target.waitFor();
    await page.waitForFunction(({view,source})=>taskFlowFixture.calls.some(r=>r.url.includes('search=Sample') && r.url.includes('source='+source))||view==='customers'&&returnTest.contactSearch==='Sample',{view,source});
    await target.scrollIntoViewIfNeeded();
    const pos=await target.evaluate(node=>{
      while(node && !node.scrollTop && !node.scrollLeft)node=node.parentElement;
      if(!node)throw Error('List did not scroll');
      returnTest.scroller=node;return [node.scrollLeft,node.scrollTop];
    });
    // Navigate via the application's real menu handlers without auto-scrolling to the menu.
    const nav=section=>page.evaluate(s=>document.querySelector('.nav [data-view="'+s+'"]').click(),section);
    await nav('profile');
    await page.evaluate(()=>{returnTest.hold=true;returnTest.generation++;});
    await nav(view);
    await page.waitForFunction(()=>returnTest.pending.length>0);
    assert(await target.isVisible(), JSON.stringify(await page.evaluate(()=>({path:location.search,summary:customerCount.textContent,search:customerSearch.value,view:document.querySelector('.view.active')?.id}))));
    assert.equal(await search.inputValue(),'Sample');
    if(isTask)assert.equal(await page.locator('#taskStatus').inputValue(),'open');
    assert.deepEqual(await page.evaluate(()=>[returnTest.scroller.scrollLeft,returnTest.scroller.scrollTop]),pos);
    await release();
    await list.getByText('Sample updated',{exact:true}).first().waitFor();
    await nav('profile');
    await page.evaluate(()=>{returnTest.status=503;});
    await nav(view);
    const notice=page.locator(isTask?(source==='native'?'#nativeTasksNotice':'#tasksNotice'):'#customersNotice');
    await page.waitForFunction(sel=>/не удалось/i.test(document.querySelector(sel).textContent),
      isTask?(source==='native'?'#nativeTasksNotice':'#tasksNotice'):'#customersNotice');
    assert(await notice.isVisible() && await target.isVisible());
    await page.evaluate(()=>document.dispatchEvent(new Event('tenant-auth-invalid')));
    await target.waitFor({state:'detached'});
    console.log('PASS return + delayed read + retained scroll + 503 + invalidation:', view,source,layout||'list');
  } finally {await page.close();}
}

(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
  try {
    for(const source of ['native','kommo']) {
      for(const layout of ['Таблица','Канбан'])await scenario(browser,'tasks',source,layout);
      await scenario(browser,'customers',source);
    }
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
