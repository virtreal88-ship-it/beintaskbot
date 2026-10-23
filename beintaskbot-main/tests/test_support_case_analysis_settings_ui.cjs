const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../docs/tenant-app.html'),'utf8');
const script=fs.readFileSync(path.join(__dirname,'../docs/tenant-ai-agents.js'),'utf8');

assert.match(html,/id="aiSupportCaseExtractionMode"/);
assert.match(html,/option value="off"/);
assert.match(html,/option value="manual"/);
assert.match(html,/option value="automatic"/);
assert.match(html,/id="aiSupportCaseChannels" multiple/);
assert.match(script,/support_case_extraction_mode/);
assert.match(script,/support_case_extraction_channels/);
assert.match(script,/extractionWrap\.hidden=extractionMode!=='automatic'/);
console.log('PASS: support-case analysis exposes explicit modes and tenant channel selection.');
