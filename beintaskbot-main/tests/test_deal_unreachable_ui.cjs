const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname,'../docs/index.html'),'utf8');
const start = html.indexOf('async function markDealUnreachable(');
const end = html.indexOf('\nfunction ',start);
assert(start >= 0 && end > start);
assert.match(html,/class="deal-unreachable[^>]+>Ulaşmaq olmadı<\/button>/);
assert.match(html,/class="deal-unreachable ml-auto shrink-0/);
assert.doesNotMatch(html,/<span[^>]*deal-drag-hint[^>]*>Sürüşdür<\/span>/);
const events = [];
let reject = false, finish;
const context = {console,
  samilDeals:[{id:7},{id:8}],samilDealsByStage:{new:[{id:7},{id:8}]},samilStageCounts:{new:2},
  renderSamilDeals:()=>events.push('render'),toast:(text,error)=>events.push(error?'error':'success'),
  loadSamilWorkspace:async()=>events.push('refresh'),
  samilDealAction:async payload=>{events.push(payload);await new Promise(resolve=>finish=resolve);if(reject)throw Error('failed');return {success:true};}};
vm.runInNewContext(html.slice(start,end),context);
(async()=>{
  const button = {disabled:false,innerHTML:'Ulaşmaq olmadı'};
  const pending = context.markDealUnreachable({id:7},button);
  assert.equal(button.disabled,true);assert.match(button.innerHTML,/yt-spin/);
  await context.markDealUnreachable({id:7},button);
  assert.equal(events.filter(e=>typeof e==='object').length,1);
  finish();await pending;
  assert.equal(context.samilDeals.length,1);assert.equal(context.samilStageCounts.new,1);
  assert.equal(button.disabled,false);assert.equal(button.innerHTML,'Ulaşmaq olmadı');
  reject=true;const failed = context.markDealUnreachable({id:8},button);finish();await failed;
  assert.equal(context.samilDeals.length,1);assert(events.includes('error'));
  console.log('PASS: unreachable button shows progress, prevents duplicate clicks and removes the deal only after success.');
})().catch(error=>{console.error(error);process.exitCode=1;});
