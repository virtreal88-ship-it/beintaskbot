const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '../docs/index.html'), 'utf8');
assert.ok(!html.includes('side-guide-editor'), 'No desktop guide navigation entry');
assert.ok(!html.includes('tab-more-guide'), 'No mobile guide navigation entry');
const approvalSection = html.slice(html.indexOf('id="guide-editor-entry"') - 500, html.indexOf('id="guide-editor-entry"') + 700);
assert.match(approvalSection, /id="guide-editor-entry"[^>]*href="\/guide-editor"/);
assert.ok(html.includes("visible(['guide-editor-entry'], canReviewNews());"), 'Keep existing approval permission');
console.log('Guide navigation checks passed');
