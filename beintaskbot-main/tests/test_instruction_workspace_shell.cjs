const { JSDOM } = require(process.env.JSDOM_PATH || '../.support-test-deps/node_modules/jsdom');
const fs = require('node:fs');
const assert = require('node:assert/strict');

const html = fs.readFileSync('docs/tenant-instructions.html', 'utf8');
const script = fs.readFileSync('docs/tenant-instruction-catalog.js', 'utf8');
const tenantId = '00000000-0000-4000-8000-000000000011';
const memberId = '11';
const publishedDoc = {
  schema_version: 1,
  title: 'Warehouse transfer',
  operation: 'inventory.warehouse_transfer',
  language: 'en',
  slides: [{
    id: 'step-1', order: 1, title: 'Select a warehouse', explanation: 'Inventory step',
    image: { asset_id: 'image-1.jpg', width: 100, height: 100 },
    areas: [], playback: { duration_ms: 6000, transition_ms: 650, focus: [], return_to_overview: true }
  }],
  subtitles: { language: 'en', slides: [[]] }
};
const guideFeed = {
  success: true,
  items: [{
    slug: '/products/inventory/warehouse', title: 'Warehouse guide', category: 'Inventory',
    body: '<script>window.catalogInjection = true</script><p>Inventory guide text.</p>',
    body_format: 'html', images: []
  }]
};
const lessonFeed = {
  success: true,
  result: {
    items: [{ id: 'lesson-1', title: 'Warehouse transfer', language: 'en', operation: 'inventory.warehouse_transfer' }],
    next_offset: null
  }
};
const lessonSnapshot = { success: true, result: { id: 'lesson-1', doc: publishedDoc } };

function makeResponse(status, body) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}
function mount() {
  const dom = new JSDOM(html, {
    url: 'https://crm.pro.az/app/instructions?site_lang=en',
    runScripts: 'dangerously',
    pretendToBeVisual: true
  });
  const calls = [];
  let mounted = null;
  dom.window.fetch = async (url) => {
    const parsed = new URL(url, dom.window.location.origin);
    calls.push(parsed);
    if (parsed.pathname === '/api/platform/me') return makeResponse(200, { member: { tenant_id: tenantId, telegram_id: Number(memberId) } });
    if (parsed.pathname === '/api/guides/public') return makeResponse(200, guideFeed);
    if (parsed.pathname === '/api/guides') return makeResponse(403, { success: false, error: 'No guide edit access' });
    if (parsed.pathname === '/api/platform/instruction-catalog') return makeResponse(200, lessonFeed);
    if (parsed.pathname === '/api/platform/instructions') return makeResponse(403, { success: false, error: 'No lesson edit access' });
    if (parsed.pathname === '/api/platform/instruction-catalog/lesson-1') return makeResponse(200, lessonSnapshot);
    throw new Error('Unexpected request: ' + parsed.pathname);
  };
  dom.window.BeinLesson = {
    mount(target, doc, options) {
      mounted = { target, doc, options };
      target.textContent = 'Rendered lesson';
      return { destroy() {} };
    }
  };
  dom.window.eval(script);
  return { dom, calls, get mounted() { return mounted; } };
}

{
  const direct = new JSDOM(html, {
    url: 'https://crm.pro.az/app/instructions?lesson=lesson-1',
    runScripts: 'dangerously'
  });
  assert.equal(direct.window.document.documentElement.classList.contains('instruction-editor-mode'), true);
  direct.window.close();
}

(async () => {
  const { dom, calls, getMounted } = (() => {
    const state = mount();
    return { dom: state.dom, calls: state.calls, getMounted: () => state.mounted };
  })();
  const { document } = dom.window;
  await new Promise(resolve => setTimeout(resolve, 10));

  assert.equal(document.documentElement.lang, 'en');
  assert.equal(document.querySelector('[role="tablist"]'), null, 'catalog uses one navigation and search, without content tabs');
  assert.equal(document.getElementById('catalogSearch').getAttribute('aria-label'), 'Search instructions');
  assert.equal(document.getElementById('newArticle').hidden, true, 'guide creation stays behind its existing permission');
  assert.equal(document.getElementById('newLesson').hidden, true, 'lesson authoring stays behind its existing permission');
  assert.equal(document.getElementById('createAI').hidden, true, 'AI lesson creation stays behind its existing permission');
  assert.equal(document.querySelectorAll('.catalog-card[data-kind="guide"]').length, 1);
  assert.equal(document.querySelectorAll('.catalog-card[data-kind="lesson"]').length, 1);

  const catalogRequest = calls.find(call => call.pathname === '/api/platform/instruction-catalog');
  assert.equal(catalogRequest.searchParams.get('expected_tenant_id'), tenantId);
  assert.equal(catalogRequest.searchParams.get('expected_user_id'), memberId);

  document.querySelector('.catalog-card[data-kind="guide"] .catalog-card-title').click();
  assert.equal(document.querySelector('.catalog-detail-heading h2').textContent, 'Warehouse guide');
  assert.equal(document.querySelector('.catalog-article-body').textContent, 'Inventory guide text.');
  assert.equal(dom.window.catalogInjection, undefined, 'Guide article HTML is sanitized before display');

  document.querySelector('.catalog-detail-tools button').click();
  document.querySelector('.catalog-card[data-kind="lesson"] .catalog-card-title').click();
  await new Promise(resolve => setTimeout(resolve, 10));
  const lessonView = getMounted();
  assert.ok(lessonView, 'published lesson is rendered with the read-only player');
  assert.equal(lessonView.doc.title, 'Warehouse transfer');
  const screenshotURL = new URL(lessonView.options.resolveAsset('image-1.jpg'), dom.window.location.origin);
  assert.equal(screenshotURL.pathname, '/api/platform/instruction-catalog/lesson-1/assets/image-1.jpg');
  assert.equal(screenshotURL.searchParams.get('expected_tenant_id'), tenantId);
  assert.equal(screenshotURL.searchParams.get('expected_user_id'), memberId);

  const search = document.getElementById('catalogSearch');
  search.value = 'inventory';
  search.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  await new Promise(resolve => setTimeout(resolve, 280));
  assert.equal(document.querySelectorAll('.catalog-card[data-kind="guide"]').length, 1);
  assert.equal(document.querySelectorAll('.catalog-card[data-kind="lesson"]').length, 1, 'the same search includes lessons and articles');

  dom.window.close();
  console.log('PASS: unified instruction tree/search, read-only employee access, safe article rendering, tenant lesson snapshots and screenshots');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
