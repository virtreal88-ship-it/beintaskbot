const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('../.support-test-deps/node_modules/jsdom');
const root = path.join(__dirname, '..');
const shell = fs.readFileSync(path.join(root, 'ui/src/app-shell.js'), 'utf8').replace("import themeCss from './app-theme.css?inline'", "const themeCss=''");
(async () => {
  for (const mobile of [false, true]) {
    const dom = new JSDOM(fs.readFileSync(path.join(root, 'docs/tenant-app.html'), 'utf8'), { url: 'https://crm.test/app', runScripts: 'outside-only' });
    const w = dom.window, d = w.document;
    w.matchMedia = q => ({ matches: q.includes('max-width') && mobile, addEventListener() {} });
    w.requestAnimationFrame = fn => w.setTimeout(fn, 0);
    w.tenantI18n = { language: 'ru', t: key => key };
    const nav = d.querySelector('#aiAnalyticsNav'), originalLabel = nav.querySelector('[data-ai-analytics-label]');
    let clicks = 0; nav.addEventListener('click', () => clicks++);
    w.eval(shell);
    for (const [language, label] of [['ru','AI-аналитика'],['az','AI analitikası'],['en','AI Analytics']]) {
      w.tenantI18n.language = language;
      d.dispatchEvent(new w.Event('tenant-language'));
      nav.hidden = false; nav.style.display = '';
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(d.querySelectorAll('[data-view="ai_analytics"]').length, 1);
      assert.equal(nav.querySelectorAll('.app-nav-label').length, 1);
      assert.equal(nav.querySelector('.app-nav-label'), originalLabel, 'reuse and style existing label, do not append a duplicate');
      assert.equal(nav.textContent.replace('◉','').trim(), label);
      assert.equal(nav.title, label);
      nav.click();
      nav.hidden = true; nav.style.display = 'none';
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(nav.hidden, true, 'permission visibility is preserved');
    }
    assert.equal(clicks, 3, 'existing route click handler is preserved');
    w.close();
  }
  console.log('PASS analytics nav: one localized label, original route and hidden state, mobile/desktop');
})().catch(error => { console.error(error); process.exitCode = 1; });
