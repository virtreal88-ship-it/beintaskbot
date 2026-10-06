const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname,'../docs/index.html'),'utf8');
const start = html.indexOf('async function markDealUnreachable(');
const end = html.indexOf('\nfunction ',start);
assert(start >= 0 && end > start);
assert.match(html,/class="deal-unreachable[^>]+>Təxirə salındı<\/button>/);
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
const helperStart = html.indexOf('function dealUnreachableButtonHtml(');
const helperEnd = html.indexOf('\nfunction ', helperStart);
context.CURRENT_USER_ID = '20';
context.hasEmployeePermission = () => true;
context.escapeHtml = value => String(value);
vm.runInNewContext(html.slice(helperStart,helperEnd),context);
assert.match(context.dealUnreachableButtonHtml({id:7},false),/data-deal-unreachable-id="7"/);
assert.equal(context.dealUnreachableButtonHtml({id:7},true),'');
context.hasEmployeePermission = () => false;
assert.equal(context.dealUnreachableButtonHtml({id:7},false),'');
context.hasEmployeePermission = () => true;
assert.match(html,/\$\{dealUnreachableButtonHtml\(deal, readonly\)\}/);
assert.match(html,/staff \? dealUnreachableButtonHtml\(deal, !!\(opts && opts.readonly\)\)/);
(async()=>{
  const button = {disabled:false,innerHTML:'Təxirə salındı'};
  const pending = context.markDealUnreachable({id:7},button);
  assert.equal(button.disabled,true);assert.match(button.innerHTML,/yt-spin/);
  await context.markDealUnreachable({id:7},button);
  await context.markDealUnreachable({id:7},{disabled:false,innerHTML:'Təxirə salındı'});
  assert.equal(events.filter(e=>typeof e==='object').length,1);
  finish();await pending;
  assert.equal(context.samilDeals.length,1);assert.equal(context.samilStageCounts.new,1);
  assert.equal(button.disabled,false);assert.equal(button.innerHTML,'Təxirə salındı');
  reject=true;const failed = context.markDealUnreachable({id:8},button);finish();await failed;
  assert.equal(context.samilDeals.length,1);assert(events.includes('error'));
  console.log('PASS: unreachable button shows progress, prevents duplicate clicks and removes the deal only after success.');
})().catch(error=>{console.error(error);process.exitCode=1;});
