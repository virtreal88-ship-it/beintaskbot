const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const html=fs.readFileSync(path.join(__dirname,'../docs/tenant-app.html'),'utf8');
const dom=new JSDOM(html);
const doc=dom.window.document;
const main=doc.querySelector('main');
for(const id of ['view-ai_agents','view-ai_testing','view-integrations','view-settings','view-profile']) {
  const view=doc.getElementById(id);
  assert.ok(view,`${id} must exist`);
  assert.equal(view.closest('main'),main,`${id} must remain inside the application main container`);
}
assert.equal(doc.getElementById('view-settings').previousElementSibling?.id,'view-integrations','settings follows the preceding app view without an orphaned footer');
console.log('PASS: settings views stay inside main and cannot be pushed below the shell by malformed AI-agent markup.');
dom.window.close();
