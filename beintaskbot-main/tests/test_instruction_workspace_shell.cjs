const { JSDOM } = require(process.env.JSDOM_PATH || '../.support-test-deps/node_modules/jsdom');
const fs = require('node:fs');
const assert = require('node:assert/strict');

const html = fs.readFileSync('docs/tenant-instructions.html', 'utf8');

function mount(url) {
  return new JSDOM(html, { url, runScripts: 'dangerously' });
}

function assertPanel(dom, activeTab) {
  const { document } = dom.window;
  const guide = document.getElementById('guideTab');
  const lessons = document.getElementById('lessonsTab');
  const guidePanel = document.getElementById('guidePanel');
  const lessonsPanel = document.getElementById('lessonsPanel');

  assert.equal(guide.getAttribute('role'), 'tab');
  assert.equal(lessons.getAttribute('role'), 'tab');
  assert.equal(guide.getAttribute('aria-selected'), String(activeTab === 'guide'));
  assert.equal(lessons.getAttribute('aria-selected'), String(activeTab === 'lessons'));
  assert.equal(guidePanel.hidden, activeTab !== 'guide');
  assert.equal(lessonsPanel.hidden, activeTab !== 'lessons');
}

{
  const dom = mount('https://example.test/app/instructions?site_lang=ru');
  const { document, KeyboardEvent } = dom.window;

  assert.equal(document.documentElement.lang, 'ru');
  assert.equal(document.getElementById('guideTab').textContent, 'База статей');
  assert.equal(document.getElementById('lessonsTab').textContent, 'Пошаговые уроки');
  assert.equal(document.querySelector('[role="tablist"]').getAttribute('aria-label'), 'Разделы инструкций');
  assert.equal(document.getElementById('guideEditorFrame').getAttribute('src'), '/guide-editor');
  assert.ok(document.getElementById('instructions'), 'existing lesson app root remains mounted');
  assertPanel(dom, 'guide');
  assert.equal(document.getElementById('guideTab').tabIndex, 0);
  assert.equal(document.getElementById('lessonsTab').tabIndex, -1);

  document.getElementById('lessonsTab').click();
  assert.equal(document.getElementById('guideTab').tabIndex, -1);
  assert.equal(document.getElementById('lessonsTab').tabIndex, 0);
  assertPanel(dom, 'lessons');
  document.getElementById('guideTab').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  assert.equal(document.activeElement.id, 'lessonsTab');
  assertPanel(dom, 'lessons');
  document.getElementById('lessonsTab').dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
  assert.equal(document.activeElement.id, 'guideTab');
  assertPanel(dom, 'guide');

  dom.window.close();
}

for (const query of ['lesson=lesson-1', 'share=share-1']) {
  const dom = mount('https://example.test/app/instructions?' + query);
  assertPanel(dom, 'lessons');
  dom.window.close();
}

{
  const dom = mount('https://example.test/app/instructions?site_lang=az');
  assert.equal(dom.window.document.documentElement.lang, 'az');
  assert.equal(dom.window.document.getElementById('guideTab').textContent, 'Məqalələr bazası');
  assert.equal(dom.window.document.querySelector('[role="tablist"]').getAttribute('aria-label'), 'Təlimat bölmələri');
  assertPanel(dom, 'guide');
  dom.window.close();
}

{
  const dom = mount('https://example.test/app/instructions?site_lang=en');
  assert.equal(dom.window.document.documentElement.lang, 'en');
  assert.equal(dom.window.document.getElementById('guideTab').textContent, 'Article library');
  assert.equal(dom.window.document.querySelector('[role="tablist"]').getAttribute('aria-label'), 'Instruction sections');
  assertPanel(dom, 'guide');
  dom.window.close();
}

console.log('PASS: instruction workspace tabs, locale labels, embedded guide route, and lesson/share deep links');
