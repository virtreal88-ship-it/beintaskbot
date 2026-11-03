/* CRM01 full built /app, actual cookie APIs and isolated native PostgreSQL. */
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {chromium} = require('../.support-test-deps/node_modules/playwright');
const origin = process.env.CONTACTS_RUNTIME_ORIGIN;
const mode = process.env.CONTACTS_RUNTIME_MODE;
const actor = Number(process.env.CONTACTS_RUNTIME_ACTOR);
const width = Number(process.env.CONTACTS_RUNTIME_WIDTH);
const language = process.env.CONTACTS_RUNTIME_LANGUAGE;
const tenantA = process.env.CONTACTS_RUNTIME_TENANT_A;
const tenantB = process.env.CONTACTS_RUNTIME_TENANT_B;
const nativeId = process.env.CONTACTS_RUNTIME_NATIVE_ID;
const relationId = process.env.CONTACTS_RUNTIME_RELATION_DEAL_ID;
const screenshotDir = process.env.CONTACTS_SCREENSHOT_DIR;
assert(origin && mode && actor && nativeId, 'Required isolated HTTP fixture is missing');

(async () => {
  const executablePath = process.env.CHROMIUM_EXECUTABLE_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined);
  const browser = await chromium.launch({headless:true, ...(executablePath ? {executablePath}:{}), args:['--no-sandbox']});
  const context = await browser.newContext({viewport:{width,height:900}, timezoneId:'Asia/Baku'});
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.setDefaultNavigationTimeout(20000);
  const errors=[], writes=[], requests=[], bundles=[], meRequests=[], refreshIntents=[], legacyTaskRefreshes=[];
  // External transports are blocked; all local cookie/me/contact/related APIs
  // execute the production handlers. No route interception manufactures ACLs.
  await context.route(url => ['http:','https:'].includes(url.protocol) && url.origin !== origin, route => route.abort());
  page.on('pageerror', error => {
    const stack=error.stack||error.message;
    errors.push(stack);
    console.error('APP PAGE ERROR: '+stack.replaceAll(origin,'<isolated-app>'));
  });
  page.on('request', request => {
    const url = new URL(request.url());
    if(url.pathname.startsWith('/api/platform/crm/native/contacts')) requests.push(request);
    if(url.pathname==='/api/platform/me') meRequests.push(request);
    if(url.pathname.startsWith('/api/platform/crm/') && (url.searchParams.has('refresh')||url.searchParams.has('full'))) {
      const intent=url.pathname+url.search;
      // The unchanged global app bootstrap refreshes Tasks on each document
      // load. It is outside CRM01; a full sync and any contact/deal refresh
      // remain forbidden throughout this contact journey.
      if(url.pathname==='/api/platform/crm/tasks'&&!url.searchParams.has('full'))legacyTaskRefreshes.push(intent);
      else refreshIntents.push(intent);
    }
    if(!['GET','HEAD'].includes(request.method())) writes.push({method:request.method(),path:url.pathname,query:Object.fromEntries(url.searchParams),body:request.postDataJSON()});
  });
  page.on('response', response => { if(new URL(response.url()).pathname === '/assets/tenant-chakra-ui.js') bundles.push(response.status()); });
  page.on('dialog', dialog => dialog.accept());
  const control = async path => {
    const response = await page.request.get(origin+'/test/'+path,{timeout:10000});
    assert.equal(response.status(),200,path);
    return response.json();
  };
  const waitBarrier = async (released=false) => {
    const deadline=Date.now()+10000;
    let state;
    do {
      state=await control('state');
      if(state.pending>0 && (!released || state.released===state.pending)) return state;
      await new Promise(resolve=>setTimeout(resolve,20));
    } while(Date.now()<deadline);
    assert.fail('Actual completed HTTP body did not reach barrier: '+JSON.stringify(state));
  };
  const current = async (tenant=tenantA) => page.waitForFunction(({tenant,actor}) =>
    window.tenantProfileSnapshot?.member?.tenant_id===tenant && Number(window.tenantProfileSnapshot.member.telegram_id)===actor,
    {tenant,actor});
  const settled = async () => {
    await page.locator('#view-customers.active').waitFor();
    await page.waitForFunction(() => document.getElementById('customersList')?.getAttribute('aria-busy')==='false' &&
      !document.getElementById('customerSearch')?.disabled && !document.getElementById('customersRefresh')?.disabled &&
      !document.querySelector('#customersList .empty[role=status]'));
  };
  const row = key => page.locator('[data-contact-key="'+key+'"]');
  const open = key => row(key).locator('[data-contact-open]').click();
  const cardBounds = async () => {
    const modal=page.locator('#nativeEntityCard.show .modal-card');
    const bounds=await modal.boundingBox();
    assert(bounds && bounds.x>=-1 && bounds.x+bounds.width<=page.viewportSize().width+1,'Contact modal fits the viewport');
    for(const button of await page.locator('#nativeEntityCard.show [data-contact-tab], #nativeEntityCard.show .crm-modal-actions button').all()) {
      if(!await button.isVisible())continue;
      const box=await button.boundingBox();
      assert(box && box.x>=bounds.x-1 && box.x+box.width<=bounds.x+bounds.width+1,
        'Visible contact tab/footer is not clipped: '+await button.innerText());
    }
  };
  const nativeCard = async () => {
    await page.locator('#nativeEntityCard.show [data-contact-tab=general]').waitFor();
    assert((await page.locator('#nativeEntityBody').innerText()).includes('Native Alpha'));
    await cardBounds();
  };
  const closeCard = async () => {
    await page.locator('#nativeEntityCard.show [data-native-card-close]').first().click();
    await page.waitForFunction(() => !document.getElementById('nativeEntityCard').classList.contains('show'));
  };
  const source = async value => {
    await page.locator('#contactsSource').selectOption(value);
    await settled();
    assert.equal(await page.locator('#contactsSource').inputValue(),value);
  };
  const search = async value => {
    const response = page.waitForResponse(response => {
      const u=new URL(response.url());
      return u.pathname==='/api/platform/crm/native/contacts' && u.searchParams.get('listing')==='1' &&
        (u.searchParams.get('search')||'')===value;
    });
    await page.locator('#customerSearch').fill(value);
    await response;
    await settled();
  };
  const popRoute = async params => {
    await page.evaluate(params => {
      const url = new URL(location.href);
      for(const key of ['contact_id','contacts_entry','deal_id','contacts_tenant','contacts_source','contacts_search','contacts_offset']) url.searchParams.delete(key);
      for(const [key,value] of Object.entries(params)) url.searchParams.set(key,value);
      url.searchParams.set('view','customers');
      history.pushState({view:'customers'},'',url);
      window.dispatchEvent(new PopStateEvent('popstate'));
    },params);
  };
  const finishHeld = async observed => {
    const before=await control('state');
    const key = value => {const url=new URL(value,origin);return JSON.stringify([url.pathname,[...url.searchParams.entries()].sort()]);};
    const heldPaths=new Set(before.held_paths.map(key));
    const held=observed.filter(request=>heldPaths.has(key(request.url())));
    assert.equal(held.length,before.pending,'Wait only on the exact server-observed delayed requests');
    assert(held.length>0,'At least one genuine HTTP JSON body must be delayed');
    const delayedResponses=await Promise.all(held.map(request=>request.response()));
    assert(delayedResponses.every(response=>response&&response.status()===200),'The actual authorized headers arrive before its JSON body');
    await control('release');
    await waitBarrier(true);
    let timer;
    try { await Promise.race([
        Promise.all(delayedResponses.map(response=>response.finished())),
        new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Delayed actual JSON body did not finish after server release')),10000);})
      ]);
    } finally { clearTimeout(timer); }
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  };
  const screenshot = async stage => {
    if(!screenshotDir || actor!==11 || mode!=='ready')return;
    fs.mkdirSync(screenshotDir,{recursive:true});
    for(const captureWidth of [360,1440]) {
      await page.setViewportSize({width:captureWidth,height:900});
      if(stage!=='native-list')await cardBounds();
      await page.screenshot({path:screenshotDir+'/crm01-'+stage+'-'+captureWidth+'.png',fullPage:false});
    }
    await page.setViewportSize({width,height:900});
  };
  try {
    await control('session?identity=initial');
    await page.goto(origin+'/app?view=customers&lang='+language);
    await current();
    await settled();
    assert.deepEqual(bundles,[200],'Actual built app bundle must be used');
    assert.equal(await page.locator('#view-customers #customerSearch').count(),1);
    assert.equal(await page.locator('#view-customers #customersList').count(),1);
    assert.equal(await page.locator('#nativeContactsList').count(),0,'There is one contact list');
    assert.deepEqual(await page.locator('#view-customers .top .crm-toolbar-actions button').evaluateAll(nodes=>nodes.map(node=>node.id).sort()),
      ['customersRefresh','nativeContactCreate'],'Contacts have no legacy force-sync control');
    assert.deepEqual(await page.locator('#contactsSource option').evaluateAll(nodes=>nodes.map(node=>node.value)),['native','kommo']);
    assert.equal(await page.locator('#view-customers').evaluate(node=>node.scrollWidth<=node.clientWidth+1),true,'No horizontal clipping');

    await screenshot('native-list');
    if(mode.startsWith('native403_')) {
      await search('Native Alpha');
      await page.locator('[data-contact-edit="'+nativeId+'"]').click();
      await page.waitForFunction(()=>!document.querySelector('#nativeContactForm [type=submit]').disabled);
      await page.locator('#nativeContactName').fill('PRIVATE UNSAVED CONTACT AFTER CURRENT REVOKE');
      const oldMe=meRequests.length;
      await control('native-revoke');
      const currentProof=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/platform/me');
      const receipt=page.waitForResponse(response=>response.request().method()==='POST' && new URL(response.url()).pathname==='/api/platform/crm/native/contacts/'+nativeId);
      await page.locator('#nativeContactForm [type=submit]').click();
      assert.equal((await receipt).status(),403);
      assert.equal((await currentProof).status(),200);
      await page.waitForFunction(mode=>JSON.stringify([...(window.tenantProfileSnapshot?.member?.permissions||[])].sort())===
        JSON.stringify(mode==='native403_view'?[]:['customers','customers:view']),mode);
      await page.waitForFunction(({mode})=> {
        const modal=document.getElementById('nativeContactModal');
        return mode==='native403_view' ? !modal.classList.contains('show') : modal.classList.contains('show') && !modal.inert && !modal.hidden;
      },{mode});
      assert(meRequests.length>oldMe,'Actual server denial must trigger fresh current-cookie /me proof');
      if(mode==='native403_view') {
        assert.equal(await page.locator('#nativeContactName').inputValue(),'');
        assert(!(await page.locator('#customersList').textContent()).includes('Native Alpha'));
        assert.equal(await page.locator('#nativeEntityCard.show').count(),0);
      } else {
        assert.equal(await page.locator('#nativeContactName').inputValue(),'PRIVATE UNSAVED CONTACT AFTER CURRENT REVOKE');
        assert.equal(await page.locator('#nativeContactForm [type=submit]').isDisabled(),true);
        assert((await page.locator('#nativeContactModalError').innerText()).length>0);
      }
      assert.equal(writes.length,1,'A denied save is not retried automatically');
    } else if(mode==='empty') {
      assert.equal(await page.locator('[data-contact-key]').count(),0);
      assert.equal((await page.locator('#customersList').innerText()).trim(),'No contacts yet.');
      assert((await page.locator('#customerCount').innerText()).includes('0'));
      assert.equal(await page.locator('#contactsPagination').isVisible(),false);
    } else if(mode==='route_origin') {
      const collectionReads = start => requests.slice(start).filter(request=>request.method()==='GET' &&
        new URL(request.url()).pathname==='/api/platform/crm/native/contacts');
      const denyBound = async start => {
        await page.locator('#customersNotice[data-contact-binding-error="true"]').waitFor();
        assert.equal(collectionReads(start).length,0,'Wrong or malformed bound links fail before listing/exact lookup');
        assert.equal(await page.locator('#nativeEntityCard.show .meta').count(),0);
        const body=page.locator('#nativeEntityBody');
        assert(!(await body.count()?await body.textContent():'').includes('B PRIVATE Kommo'));
      };
      await source('kommo');
      await open('kommo:100:501');
      await page.locator('#nativeEntityCard.show .meta').waitFor();
      const originalText=await page.locator('#nativeEntityBody').innerText();
      assert(!originalText.includes('B PRIVATE Kommo'));
      const selectedA=page.url();
      assert.equal(new URL(selectedA).searchParams.get('contacts_tenant'),tenantA);
      assert.equal(await page.evaluate(()=>history.state?.contactsTenant),tenantA);
      const switchedAt=requests.length;
      await control('session?identity=company_b');
      await page.evaluate(()=>window.tenantRequestCoordinator.revalidateProfile());
      await current(tenantB);
      await settled();
      assert.equal(await page.locator('#nativeEntityCard.show').count(),0,'Identity change clears the selected A frame');
      assert.equal(collectionReads(switchedAt).filter(request=>new URL(request.url()).searchParams.has('row_key')).length,0,
        'Identical provider IDs must not auto-select a distinct B contact');
      assert.equal(new URL(page.url()).searchParams.get('contacts_entry'),null);
      assert.equal(new URL(page.url()).searchParams.get('lang'),'en','Unrelated route context is preserved');
      let start=requests.length;
      await page.goto(selectedA);
      await current(tenantB);
      await denyBound(start);
      for(const malformed of ['not-a-uuid','','duplicate']) {
        const copied=new URL(selectedA);
        copied.searchParams.set('contacts_tenant',malformed==='duplicate'?tenantB:malformed);
        if(malformed==='duplicate')copied.searchParams.append('contacts_tenant',tenantA);
        start=requests.length;
        await page.goto(copied.href);
        await current(tenantB);
        await denyBound(start);
      }
      await control('session?identity=initial');
      await page.goto(selectedA);
      await current();
      await page.locator('#nativeEntityCard.show .meta').waitFor();
      assert.equal(await page.locator('#nativeEntityBody').innerText(),originalText,'A copied bound A URL still opens its original object under A');
      await page.evaluate(()=>window.tenantAppNavigate('profile',true));
      await page.goBack();
      await page.locator('#nativeEntityCard.show .meta').waitFor();
      assert.equal(new URL(page.url()).searchParams.get('contacts_tenant'),tenantA);
      assert.equal(await page.locator('#nativeEntityBody').innerText(),originalText,'Back restores the authorized A object');
      await page.evaluate(()=>window.tenantAppNavigate('profile',true));
      await control('session?identity=company_b');
      await page.evaluate(()=>window.tenantRequestCoordinator.revalidateProfile());
      await current(tenantB);
      start=requests.length;
      await page.goBack();
      await denyBound(start);
      const legacy=new URL(selectedA);
      legacy.searchParams.delete('contacts_tenant');
      start=requests.length;
      await page.goto(legacy.href);
      await current(tenantB);
      await page.locator('#nativeEntityCard.show .meta').waitFor();
      assert((await page.locator('#nativeEntityBody').innerText()).includes('B PRIVATE Kommo'),
        'Old unqualified links intentionally retain current-company semantics');
      assert(collectionReads(start).some(request=>new URL(request.url()).searchParams.get('expected_tenant_id')===tenantB));
    } else if(mode==='workflow_revoke') {
      console.log('workflow: select current source');
      await source('kommo');
      const permissions=await page.evaluate(()=>tenantProfileSnapshot.member.permissions);
      await control('hold?kind=card');
      const start=requests.length;
      await open('kommo:100:501');
      console.log('workflow: card click returned');
      await waitBarrier();
      const held=requests.slice(start);
      assert.equal((await page.locator('#nativeEntityBody').innerText()).includes('Visible newest contact'),false);
      await control('workflow-revoke');
      console.log('workflow: actual stage edit committed');
      await page.evaluate(()=>Promise.race([window.tenantRequestCoordinator.revalidateProfile(),new Promise((_,reject)=>setTimeout(()=>reject(Error('Current /me revalidation did not finish')),10000))]));
      console.log('workflow: current profile loaded');
      await settled();
      assert.deepEqual(await page.evaluate(()=>tenantProfileSnapshot.member.permissions),permissions,'Only workflow stage visibility changed');
      assert.equal(await page.locator('#nativeEntityCard.show').count(),0,
        JSON.stringify(await page.locator('#nativeEntityCard').evaluate(node=>({title:node.querySelector('#nativeEntityTitle')?.textContent,body:node.querySelector('#nativeEntityBody')?.textContent,status:node.querySelector('#contactsCardStatus')?.textContent}))));
      await finishHeld(held);
      console.log('workflow: old card body released');
      assert.equal(await page.locator('#nativeEntityCard.show').count(),0);
      assert(!(await page.locator('#nativeEntityBody').textContent()).includes('Visible newest contact'));
      assert.equal(await page.locator('[data-contact-key]').count(),0);
    } else if(mode==='tenant_switch') {
      await search('Native Alpha');
      await control('hold?kind=card');
      const start=requests.length;
      // This is an existing unqualified direct link, distinct from the new
      // origin-bound card URL covered by the route_origin journey.
      await popRoute({contacts_source:'native',contact_id:nativeId,contacts_search:'Native Alpha'});
      await waitBarrier();
      const held=requests.slice(start);
      await control('session?identity=company_b');
      await page.evaluate(()=>window.tenantRequestCoordinator.revalidateProfile());
      await current(tenantB);
      await settled();
      assert((await page.locator('#customersList').innerText()).includes('B PRIVATE Native'));
      assert(!(await page.locator('#nativeEntityCard').textContent()).includes('Native Alpha'));
      const freshTarget=page.waitForResponse(response=> {
        const url=new URL(response.url());
        return url.pathname==='/api/platform/crm/native/contacts/'+nativeId && url.searchParams.get('expected_tenant_id')===tenantB;
      });
      // Explicitly open that old legacy URL under B. It keeps its documented
      // current-company semantics, and the foreign UUID must resolve to404.
      await popRoute({contacts_source:'native',contact_id:nativeId});
      assert.equal((await freshTarget).status(),404,'A persisted foreign contact link is rechecked under the new cookie');
      await page.locator('#contactsCardStatus[role=alert]').waitFor();
      assert(!(await page.locator('#nativeEntityCard').textContent()).includes('Native Alpha'));
      await finishHeld(held);
      assert(!(await page.locator('#nativeEntityBody').textContent()).includes('Native Alpha'));
      assert(!(await page.locator('#customersList').innerText()).includes('Native Alpha'));
    } else if(mode==='route_aba') {
      await source('kommo');
      await control('hold?kind=card');
      const start=requests.length;
      await popRoute({contacts_source:'kommo',contacts_entry:'kommo:100:501'});
      await waitBarrier();
      const held=requests.slice(start);
      await popRoute({contacts_source:'kommo',contacts_entry:'kommo:200:501'});
      await page.locator('#nativeEntityBody').filter({hasText:'Other account same contact'}).waitFor();
      await finishHeld(held);
      assert((await page.locator('#nativeEntityBody').innerText()).includes('Other account same contact'));
      assert(!(await page.locator('#nativeEntityBody').innerText()).includes('Visible newest contact'));
      assert.equal(new URL(page.url()).searchParams.get('contacts_entry'),'kommo:200:501');
    } else if(mode==='states') {
      await control('hold?kind=list');
      await page.locator('#customersRefresh').click();
      await waitBarrier();
      assert.equal(await page.locator('#customersList').getAttribute('aria-busy'),'true');
      assert(!/^0$/.test(await page.locator('#customerCount').innerText()),'Loading is not a false zero count');
      await control('release');
      await settled();
      await search('Definitely nonexistent isolated contact');
      assert.equal(await page.locator('[data-contact-key]').count(),0);
      assert((await page.locator('#customersList').innerText()).includes('No matches'));
      await search('');
      await control('fault');
      await page.locator('#customersRefresh').click();
      await page.locator('#contactsRetry').waitFor();
      assert.equal(await page.locator('#customerCount').innerText(),'—');
      await page.locator('#contactsRetry').click();
      await settled();
      assert.equal(await page.locator('[data-contact-key]').count(),50);
    } else {
      const nativeAllowed=actor!==84;
      if(nativeAllowed) {
        await page.locator('.app-avatar-button').click();
        await page.locator('[data-app-open=profile]').click();
        const menu=page.locator('.app-mobile-menu');
        if(await menu.isVisible() && await menu.getAttribute('aria-expanded')!=='true') await menu.click();
        const group=page.locator('.app-nav-group[data-group=crm] .app-nav-group-title');
        if(await group.getAttribute('aria-expanded')==='false') await group.click();
        await page.locator('.nav [data-view=customers]').click();
        await settled();
        assert.equal(await page.locator('[data-contact-key]').count(),50);
        if(actor===81) {
          await popRoute({contacts_source:'native',contacts_offset:'999'});
          await page.locator('#contactsFirstPage').waitFor();
          assert.equal(await page.locator('[data-contact-key]').count(),0);
          assert((await page.locator('#customerCount').innerText()).includes('61'));
          await page.locator('#contactsFirstPage').click();
          await settled();
          assert.equal(await page.locator('[data-contact-key]').count(),50);
        }
        const first=await page.locator('[data-contact-key]').first().getAttribute('data-contact-key');
        await page.locator('#contactsNext').click();
        await settled();
        assert.equal(await page.locator('[data-contact-key]').count(),11);
        assert.equal(new URL(page.url()).searchParams.get('contacts_offset'),'50');
        const pageKey=await page.locator('[data-contact-key]').first().getAttribute('data-contact-key');
        await page.mouse.wheel(0,500);
        await open(pageKey);
        await page.locator('#nativeEntityCard.show [data-contact-tab=general]').waitFor();
        const savedScroll=await page.evaluate(()=>history.state?.contacts?.scroll);
        await page.goBack();
        await settled();
        assert.equal(await page.locator('#nativeEntityCard.show').count(),0);
        assert.equal(new URL(page.url()).searchParams.get('contacts_offset'),'50');
        assert.equal(await page.locator('[data-contact-key]').first().getAttribute('data-contact-key'),pageKey);
        await page.waitForFunction(scroll=>Math.abs(window.scrollY-scroll)<3,savedScroll);
        assert.notEqual(pageKey,first);
        await search('Native Alpha');
        assert.equal(await page.locator('[data-contact-key]').count(),1);
        assert((await page.locator('#customerCount').innerText()).includes('1'));
        await open('native:'+nativeId);
        await nativeCard();
        await page.reload();
        await current();
        await nativeCard();
        await screenshot('native-card');
        if(actor===11) {
          await page.locator('#nativeEntityCard [data-native-card-edit]').click();
          await page.waitForFunction(()=>!document.querySelector('#nativeContactForm [type=submit]').disabled);
          await page.locator('#nativeContactName').click();
          assert.equal(await page.locator('#nativeContactName').evaluate(node=> {
            const box=node.getBoundingClientRect();
            return document.elementFromPoint(box.left+box.width/2,box.top+box.height/2)===node;
          }),true,'Owned card must not cover the editor');
          await page.keyboard.press('Escape');
          await nativeCard();
          // A modal intentionally covers the topbar. This is the actual
          // application's public route entry, used by direct navigation,
          // rather than a forced click through its backdrop.
          await page.evaluate(()=>window.tenantAppNavigate('profile',true));
          const menu=page.locator('.app-mobile-menu');
          if(await menu.isVisible()&&await menu.getAttribute('aria-expanded')!=='true')await menu.click();
          await page.locator('.nav [data-view=customers]').click();
          await nativeCard();
        }
        if(actor===85) {
          await page.locator('[data-contact-tab=deals]').click();
          await page.locator('[data-contact-panel=deals] [data-contact-related-status]').filter({hasText:'Current permissions'}).waitFor();
          assert((await page.locator('#nativeEntityBody').innerText()).includes('Native Alpha'));
          assert.equal(await page.locator('#nativeEntityCard [data-native-card-edit]').isVisible(),false);
        }
        await closeCard();
        await search('');
      } else {
        assert.equal(await page.locator('#contactsSource').inputValue(),'kommo');
        assert.equal(await page.locator('#nativeContactCreate').isVisible(),false);
        assert.equal(await page.locator('#contactsSource option[value=native]').evaluate(node=>node.disabled),true);
        await page.locator('.app-sidebar-header .app-icon-button').click();
        assert.equal(await page.locator('body').getAttribute('data-sidebar'),'collapsed');
      }
      await source('kommo');
      const externalKeys=await page.locator('[data-contact-key]').evaluateAll(nodes=>nodes.map(node=>node.dataset.contactKey));
      assert(externalKeys.includes('kommo:100:501'));
      assert(!externalKeys.includes('kommo:100:550'));
      await open('kommo:200:501');
      await page.locator('#nativeEntityBody').filter({hasText:'Other account same contact'}).waitFor();
      await cardBounds();
      await screenshot('external-card');
      assert.equal(await page.locator('#nativeEntityCard [data-native-card-edit]').isVisible(),false);
      await closeCard();
      await popRoute({contacts_source:'kommo',deal_id:'109'});
      await page.locator('#nativeEntityBody').filter({hasText:'Explicit Connect cached context'}).waitFor();
      assert((await page.locator('#nativeEntityBody').innerText()).includes(language==='en'?'Saved deal context':language==='az'?'Saxlanılan sövdələşmənin konteksti':'Контекст сохранённой сделки'));
      await page.reload();
      await current();
      await page.locator('#nativeEntityBody').filter({hasText:'Explicit Connect cached context'}).waitFor();
      await closeCard();
      if(actor===11) {
        await source('native');
        await search('Browser isolated');
        await page.locator('#nativeContactCreate').click();
        await page.locator('#nativeContactName').fill('Browser isolated created');
        await page.locator('#nativeContactPhone').fill('+994501110001');
        await page.locator('#nativeContactEmail').fill('browser-isolated@native.example');
        await page.waitForFunction(()=>!document.querySelector('#nativeContactForm [type=submit]').disabled);
        const newId=await page.locator('#nativeContactId').inputValue();
        await control('malformed-write');
        await page.locator('#nativeContactForm [type=submit]').click();
        await page.locator('#contactsCheckSave').waitFor();
        assert.equal(await page.locator('#nativeContactForm [type=submit]').isDisabled(),true);
        assert.equal(writes.length,1,'Malformed successful HTTP confirmation cannot cause a second create');
        await page.locator('#contactsCheckSave').click();
        await page.waitForFunction(()=>!document.getElementById('nativeContactModal').classList.contains('show'));
        await settled();
        assert.equal(writes.length,1,'Exact UUID readback confirms the existing create');
        await open('native:'+newId);
        await page.locator('#nativeEntityBody').filter({hasText:'Browser isolated created'}).waitFor();
        await page.locator('[data-contact-tab=deals]').click();
        await page.locator('[data-contact-deal-select]').selectOption(relationId);
        let relationReceipt=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/platform/crm/native/deals/'+relationId);
        await page.locator('[data-contact-link-deal]').click();
        assert.equal((await relationReceipt).status(),200);
        await page.locator('[data-contact-tab=general].active').waitFor();
        await page.locator('[data-contact-tab=deals]').click();
        await page.locator('[data-contact-unlink-deal="'+relationId+'"]').waitFor();
        relationReceipt=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/platform/crm/native/deals/'+relationId);
        await page.locator('[data-contact-unlink-deal="'+relationId+'"]').click();
        assert.equal((await relationReceipt).status(),200);
        await page.locator('[data-contact-tab=general].active').waitFor();
        await page.locator('[data-contact-tab=deals]').click();
        await page.locator('[data-contact-deal-select]').waitFor();
        assert.equal(await page.locator('[data-contact-unlink-deal]').count(),0);
        await closeCard();
        await page.locator('[data-contact-edit="'+newId+'"]').click();
        await page.waitForFunction(()=>!document.querySelector('#nativeContactForm [type=submit]').disabled);
        await page.locator('#nativeContactName').fill('Browser isolated edited');
        await page.locator('#nativeContactForm [type=submit]').click();
        await page.waitForFunction(()=>!document.getElementById('nativeContactModal').classList.contains('show'));
        await settled();
        await open('native:'+newId);
        await page.locator('#nativeEntityBody').filter({hasText:'Browser isolated edited'}).waitFor();
        const archiveReceipt=page.waitForResponse(response=>response.request().method()==='POST' && new URL(response.url()).pathname==='/api/platform/crm/native/contacts/'+newId);
        await page.locator('#nativeEntityCard [data-native-card-archive]').click();
        assert.equal((await archiveReceipt).status(),200);
        await page.waitForFunction(()=>!document.getElementById('nativeEntityCard').classList.contains('show'));
        await settled();
        assert.equal(await row('native:'+newId).count(),0);
      }
    }
    if(!(mode==='ready'&&actor===11)&&!mode.startsWith('native403_')) assert.deepEqual(writes,[],'Reading, navigation and cached history send no mutations');
    else if(mode==='ready'&&actor===11) {
      assert.equal(writes.length,5);
      assert(writes.every(write=>write.method==='POST' && (write.path.startsWith('/api/platform/crm/native/contacts') || write.path==='/api/platform/crm/native/deals/'+relationId) && write.query.expected_tenant_id===tenantA && write.query.expected_user_id===String(actor)));
    }
    assert.deepEqual(errors,[],'The actual full app must have no page errors');
    for(const request of requests.filter(item=>item.method()==='GET')) {
      const url=new URL(request.url());
      assert(!url.searchParams.has('refresh')&&!url.searchParams.has('full'),'Contact reads never request provider refresh');
      if(url.pathname==='/api/platform/crm/native/contacts')assert.equal(url.searchParams.get('listing'),'1','The directory uses its single scoped listing');
    }
    assert(legacyTaskRefreshes.every(path=>path==='/api/platform/crm/tasks?refresh=1'),'Retained Task requests have only the existing bootstrap shape');
    console.log('Unchanged Task bootstrap requests: '+JSON.stringify(legacyTaskRefreshes));
    if(screenshotDir) {
      fs.mkdirSync(screenshotDir,{recursive:true});
      fs.writeFileSync(screenshotDir+'/crm01-network-'+mode+'-'+actor+'-'+width+'.json',
        JSON.stringify({legacyTaskRefreshes,forbiddenContactOrFullRefresh:refreshIntents,contactReadCount:requests.filter(item=>item.method()==='GET').length},null,2)+'\n');
    }
    assert.deepEqual(refreshIntents,[],'Normal contact reload, search and history never start external full sync');
    console.log(`PASS CRM01 actual PostgreSQL HTTP → built /app ${mode}, ${actor}, ${width}px, ${language}`);
  } finally { await context.close(); await browser.close(); }
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
