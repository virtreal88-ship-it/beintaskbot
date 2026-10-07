const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '../docs/index.html'), 'utf8');
assert.ok(!html.includes('side-guide-editor'), 'No desktop guide navigation entry');
assert.ok(!html.includes('tab-more-guide'), 'No mobile guide navigation entry');
const approvalSection = html.slice(html.indexOf('id="guide-editor-entry"') - 500, html.indexOf('id="guide-editor-entry"') + 700);
assert.match(approvalSection, /id="guide-editor-entry"[^>]*href="\/guide-editor"/);
assert.ok(html.includes("visible(['guide-editor-entry'], canReviewNews());"), 'Keep existing approval permission');
const guideIndex=html.indexOf('id="guide-editor-entry"');
const pendingIndex=html.indexOf('id="screen-pending"');
assert(guideIndex>pendingIndex,'Guide belongs inside approvals, never before all screens');
assert(!html.slice(html.indexOf('id="content-area"'),pendingIndex).includes('id="guide-editor-entry"'));
assert(html.includes('body.h-screen { height:100vh; height:100dvh; }'),'Use visible mobile viewport');
assert(html.includes('#content-area { min-height:0; box-sizing:border-box; padding-bottom:calc(80px + env(safe-area-inset-bottom, 0px)); }'));
assert(html.includes('padding-bottom:0 !important; height:100% !important;'),'Open chat fits allocated viewport');
console.log('Guide navigation checks passed');
