const fs=require('node:fs'),assert=require('node:assert/strict');
const script=fs.readFileSync('docs/tenant-knowledge-candidates.js','utf8');
const source=script.slice(script.indexOf('  function mediaStatusText('),script.indexOf('  function mediaReasonText('));
const status=new Function('lang',source+'; return mediaStatusText;')(()=> 'en');
assert.equal(status({status:'partial',kind:'video',audio_status:'processed',extraction_status:'completed'}),'Partially processed');
assert.equal(status({status:'pending_consent'}),'Awaiting consent');
assert.equal(status({status:'unknown',code:'provider_result_unknown'}),'Outcome unknown; no automatic retry');
assert.equal(status({status:'unknown',code:'status_not_recorded'}),'Status unverified');
assert.equal(status({status:'error'}),'Processing error');
assert.equal(status({status:'processed',complete:true,extraction_status:'completed'}),'Processed by AI');
assert.notEqual(status({status:'processed',complete:false,extraction_status:'completed'}),'Processed by AI');
console.log('PASS case media UI: partial audio/video, consent, unknown, errors, and no false complete');

// Reuse the existing card fixture up to its first modal open, independently of
// unrelated legacy layout selectors later in that suite.
let fixture=fs.readFileSync('tests/test_tenant_knowledge_candidates_card_ui.cjs','utf8');
fixture=fixture.slice(0,fixture.indexOf(" assert.equal(d.getElementById('caseMethod')"));
fixture=fixture.replace("clarification_note:'Please confirm the result',", "clarification_note:'Please confirm the result',media_coverage:{complete:false,total:2,boundary_status:'verified',scope:'case_interval'},");
fixture=fixture.replace('(async()=>','return (async()=>');
fixture+="assert(d.querySelector('[data-case-media-coverage]'),'partial banner rendered in real case modal');assert(!requests.some(r=>r.opt.method==='POST'),'opening coverage cannot start media processing');dom.window.close();}})();";
new Function('require',fixture)(require).then(()=>console.log('PASS case modal coverage in RU/AZ/EN; no analysis requests')).catch(e=>{console.error(e);process.exitCode=1;});
