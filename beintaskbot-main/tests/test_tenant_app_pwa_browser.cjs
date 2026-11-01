/* Real Chromium install-surface test. Uses local fixtures only; no auth, API writes or push permission. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const docs = path.join(root, 'docs');
const source = name => fs.readFileSync(path.join(docs, name), 'utf8');
const mime = name => name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : name.endsWith('.webmanifest') ? 'application/manifest+json' : name.endsWith('.png') ? 'image/png' : name.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
let workerVersion = 1;
let assetRequests = 0;
let apiRequests = 0;

const html = source('tenant-app.html');
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const send = (status, type, body, headers = {}) => { res.writeHead(status, {'content-type': type, ...headers}); res.end(body); };
  if (url.pathname === '/app' || url.pathname === '/app/') return send(200, 'text/html; charset=utf-8', html, {'cache-control':'no-store'});
  if (url.pathname === '/app/push-test') return send(200, 'text/html; charset=utf-8', '<!doctype html><title>Push worker fixture</title>', {'cache-control':'no-store'});
  if (url.pathname === '/app/manifest.webmanifest') return send(200, 'application/manifest+json', source('tenant-app.webmanifest'), {'cache-control':'no-store'});
  if (url.pathname === '/app/pwa-sw.js') {
    const body = source('tenant-app-pwa-sw.js').replace('beintaskbot-app-pwa-v1', `beintaskbot-app-pwa-v${workerVersion}`);
    return send(200, 'text/javascript', body, {'cache-control':'no-store', 'service-worker-allowed':'/app'});
  }
  if (url.pathname === '/app/push-sw.js') return send(200, 'text/javascript', source('tenant-push-sw.js'), {'cache-control':'no-store', 'service-worker-allowed':'/app/'});
  if (url.pathname === '/app/pwa-icon-192.png') return send(200, 'image/png', fs.readFileSync(path.join(docs, 'icon-192.png')));
  if (url.pathname === '/app/pwa-icon-512.png') return send(200, 'image/png', fs.readFileSync(path.join(docs, 'icon-512.png')));
  if (url.pathname === '/assets/tenant-pwa.js') return send(200, 'text/javascript', source('tenant-pwa.js'), {'cache-control':'no-store'});
  if (url.pathname === '/assets/pwa-probe.js') { assetRequests++; return send(200, 'text/javascript', 'window.__pwaProbeLoaded=(window.__pwaProbeLoaded||0)+1;', {'cache-control':'no-store'}); }
  if (url.pathname === '/sw.js') return send(200, 'text/javascript', source('sw.js'), {'cache-control':'no-store'});
  if (url.pathname.startsWith('/api/')) {
    apiRequests++;
    if (url.pathname === '/api/platform/me') return send(200, 'application/json', JSON.stringify({success:true,member:{tenant_id:'fixture-tenant',telegram_id:123,role:'owner',display_name:'PWA fixture',active:true},capabilities:{modules:{}}}), {'cache-control':'no-store'});
    if (req.method === 'GET') return send(200, 'application/json', JSON.stringify({tenant:url.searchParams.get('tenant')||'default',private_value:'fixture-only'}), {'cache-control':'no-store'});
    return send(200, 'application/json', '{"success":true}', {'cache-control':'no-store'});
  }
  if (url.pathname.startsWith('/assets/')) {
    const file = path.join(docs, path.basename(url.pathname));
    if (fs.existsSync(file)) return send(200, mime(file), fs.readFileSync(file), {'cache-control':'no-store'});
  }
  if(process.env.DEBUG_PWA)console.log('fixture 404:',req.method,url.pathname);
  return send(404, 'text/plain', 'not found');
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  const origin = `http://127.0.0.1:${address.port}`;
  const chromiumPath=process.env.CHROMIUM_EXECUTABLE_PATH||process.env.CHROMIUM_PATH;
  const browser = await chromium.launch({headless:true,args:['--no-sandbox'],...(chromiumPath?{executablePath:chromiumPath}:{})});
  try {
    const context = await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    const pushSetupPage=await context.newPage();
    await pushSetupPage.goto(origin+'/app/push-test',{waitUntil:'domcontentloaded'});
    const pushScope=await pushSetupPage.evaluate(async()=>new URL((await navigator.serviceWorker.register('/app/push-sw.js',{scope:'/app/'})).scope).pathname);
    assert.equal(pushScope,'/app/');
    await pushSetupPage.waitForFunction(async()=>!!(await navigator.serviceWorker.getRegistration('/app/'))?.active);
    const pushRegistration=await pushSetupPage.evaluate(async()=>{const reg=await navigator.serviceWorker.getRegistration('/app/');return {scope:new URL(reg.scope).pathname,script:new URL(reg.active.scriptURL).pathname};});
    assert.equal(pushRegistration.script,'/app/push-sw.js');
    const pushBefore=await pushSetupPage.evaluate(()=>({permission:Notification.permission,hasPermissionApi:typeof Notification.requestPermission==='function'}));
    await pushSetupPage.close();
    const page = await context.newPage();
    await page.addInitScript(() => {
      const key = 'tenant-app-pwa-page-load-count';
      sessionStorage.setItem(key, String(Number(sessionStorage.getItem(key) || '0') + 1));
    });
    if(process.env.DEBUG_PWA){page.on('console',message=>console.log('browser console:',message.type(),message.text()));page.on('pageerror',error=>console.log('browser error:',error.message));page.on('requestfailed',request=>console.log('request failed:',request.url(),request.failure()?.errorText));}
    await page.goto(origin+'/app?view=dialogs',{waitUntil:'domcontentloaded'});
    if(process.env.DEBUG_PWA)console.log('app pwa state:',await page.evaluate(()=>({ready:typeof window.tenantAppPwaReady,script:document.querySelector('script[src="/assets/tenant-pwa.js"]')?.outerHTML,head:document.querySelector('link[rel="manifest"]')?.href})));
    await page.waitForFunction(()=>window.tenantAppPwaReady instanceof Promise);
    await page.waitForFunction(async()=>{
      const registration=await navigator.serviceWorker.getRegistration('/app');
      return !!registration?.active && !!navigator.serviceWorker.controller;
    });
    const criteria = await page.evaluate(async()=>{
      const manifest=await (await fetch('/app/manifest.webmanifest',{cache:'no-store'})).json();
      const registration=await navigator.serviceWorker.getRegistration('/app');
      return {manifest,scope:new URL(registration.scope).pathname,script:new URL(registration.active.scriptURL).pathname,secure:isSecureContext};
    });
    assert.equal(criteria.secure,true);
    assert.equal(criteria.scope,'/app');
    assert.equal(criteria.script,'/app/pwa-sw.js');
    assert.equal(criteria.manifest.start_url,'/app');
    assert.equal(criteria.manifest.display,'standalone');
    assert.deepEqual(criteria.manifest.icons.map(icon=>icon.sizes).sort(),['192x192','512x512']);

    const pushAfter = await page.evaluate(async()=>{const reg=await navigator.serviceWorker.getRegistration('/app/');return {permission:Notification.permission,subscription:await reg.pushManager.getSubscription(),scope:new URL(reg.scope).pathname,script:new URL(reg.active.scriptURL).pathname};});
    assert.equal(pushAfter.permission,pushBefore.permission,'PWA setup must not change notification permission');
    assert.equal(pushAfter.subscription,null,'PWA setup must not create a push subscription');
    assert.equal(pushAfter.scope,pushRegistration.scope,'PWA worker must not replace the push worker scope');
    assert.equal(pushAfter.script,pushRegistration.script,'PWA setup must preserve the existing push worker');

    await page.evaluate(()=>{const script=document.createElement('script');script.src='/assets/pwa-probe.js';document.body.append(script);});
    await page.waitForFunction(()=>window.__pwaProbeLoaded===1);
    assert.equal(assetRequests,1);
    await page.evaluate(()=>{const script=document.createElement('script');script.src='/assets/pwa-probe.js?token=fixture-secret';document.body.append(script);});
    await page.waitForFunction(()=>window.__pwaProbeLoaded===2);
    await page.evaluate(async()=>{
      await caches.open('beintaskbot-app-pwa-v0');
      await caches.open('beintaskbot-webapp-v-test');
      await caches.open('beintaskbot-media-v1');
    });
    await context.setOffline(true);
    const offlineAsset = await page.evaluate(async()=>{
      const script=document.createElement('script');script.src='/assets/pwa-probe.js';document.body.append(script);
      await new Promise((resolve,reject)=>{script.onload=resolve;script.onerror=()=>reject(Error('cached public script missing'));});
      return {loaded:window.__pwaProbeLoaded,token:await caches.open('beintaskbot-app-pwa-v1').then(cache=>cache.keys()).then(keys=>keys.some(key=>key.url.includes('fixture-secret')))};
    });
    assert.equal(offlineAsset.loaded,3,'safe public asset can use its app cache on a network failure');
    assert.equal(offlineAsset.token,false,'asset query strings are not persisted as cache keys');
    const privateOffline = await page.evaluate(async()=>{
      try { await fetch('/api/platform/private?tenant=other'); return 'unexpected-response'; }
      catch { return 'network-failed'; }
    });
    assert.equal(privateOffline,'network-failed','CRM API must never fall back to cached tenant data');
    const mediaOffline = await page.evaluate(async()=>{
      try { await fetch('/api/platform/crm/chat/attachment?id=fixture'); return 'unexpected-response'; }
      catch { return 'network-failed'; }
    });
    assert.equal(mediaOffline,'network-failed','CRM media must never fall back to Cache API');
    await context.setOffline(false);
    const tenantData = await page.evaluate(async()=>{
      const a=await (await fetch('/api/platform/private?tenant=A',{cache:'no-store'})).json();
      const b=await (await fetch('/api/platform/private?tenant=B',{cache:'no-store'})).json();
      return [a.tenant,b.tenant];
    });
    assert.deepEqual(tenantData,['A','B'],'tenant data is fetched per request, never read from app cache');
    await page.evaluate(()=>fetch('/api/platform/auth/logout',{method:'POST',headers:{'content-type':'application/json'},body:'{}'}));
    assert.ok(apiRequests>=3,'online tenant reads and logout reach the network');

    const cachesBefore = await page.evaluate(()=>caches.keys());
    assert.ok(cachesBefore.includes('beintaskbot-app-pwa-v1'));
    assert.ok(cachesBefore.includes('beintaskbot-app-pwa-v0'));
    assert.ok(cachesBefore.includes('beintaskbot-webapp-v-test'));
    assert.ok(cachesBefore.includes('beintaskbot-media-v1'));

    await page.evaluate(() => { window.__unsentWorkFixture = 'unsent draft stays in the page'; });
    workerVersion = 2;
    await page.evaluate(async()=>{const registration=await navigator.serviceWorker.getRegistration('/app');await registration.update();});
    await page.waitForFunction(async()=>{
      const keys=await caches.keys();
      const registration=await navigator.serviceWorker.getRegistration('/app');
      return registration?.active?.scriptURL && !keys.includes('beintaskbot-app-pwa-v1') && !keys.includes('beintaskbot-app-pwa-v0');
    },null,{timeout:10000});
    const cachesAfter = await page.evaluate(()=>caches.keys());
    assert.ok(cachesAfter.includes('beintaskbot-webapp-v-test'),'app activation preserves the legacy webapp cache');
    assert.ok(cachesAfter.includes('beintaskbot-media-v1'),'app activation preserves legacy media cache');
    const pageStateAfterUpdate = await page.evaluate(() => ({
      loadCount: Number(sessionStorage.getItem('tenant-app-pwa-page-load-count') || '0'),
      unsentWork: window.__unsentWorkFixture
    }));
    assert.equal(pageStateAfterUpdate.loadCount, 1, 'service worker activation does not automatically reload the app page');
    assert.equal(pageStateAfterUpdate.unsentWork, 'unsent draft stays in the page', 'worker updates preserve in-memory work');

    await page.evaluate(async()=>{
      await caches.open('beintaskbot-app-pwa-v2');
      await caches.open('beintaskbot-v2026-09-30-triage-actions-wa-fix');
    });
    await page.evaluate(async()=>navigator.serviceWorker.register('/sw.js',{scope:'/'}));
    await page.waitForFunction(async()=>!!(await navigator.serviceWorker.getRegistration('/'))?.active);
    const cachesAfterLegacyActivation = await page.evaluate(()=>caches.keys());
    assert.ok(cachesAfterLegacyActivation.includes('beintaskbot-app-pwa-v2'),'legacy /webapp activation preserves /app PWA cache');
    await page.waitForFunction(()=>navigator.serviceWorker.controller?.scriptURL.endsWith('/app/pwa-sw.js'));

    const workerSource = source('tenant-app-pwa-sw.js');
    assert.match(workerSource,/startsWith\(APP_CACHE_PREFIX\)/);
    console.log('tenant-app PWA browser regression passed: /app install surface, distinct push registration preserved, static-only cache fallback, API/media bypass, account freshness and isolated update cleanup');
  } finally {
    await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
