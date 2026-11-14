const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('docs/tenant-operator-dialogs.js','utf8');
const start=source.indexOf('  function retainDeliveryEvidence('),end=source.indexOf('  function deliveryMarkup(',start);
const context={sameDialog:(a,b)=>JSON.stringify(a)===JSON.stringify(b)};vm.createContext(context);vm.runInContext(source.slice(start,end),context);
const row=(status,changes={})=>({id:'row',external_id:'connect:id',source:'connect',source_channel_id:'a',source_conversation_id:'g',source_lead_id:1,direction:'outgoing',delivery_state:status,...changes});
const page=(r,dialog={channel_id:'a',conversation_id:'g'})=>({dialog,messages:[r]});
for(const [before,after,expected] of [['read','pending','read'],['sent',undefined,'sent'],['delivered','sent','delivered'],['sent','pending','sent'],['sent','delivered','delivered'],['failed','sent','failed'],['failed','read','read']]){
 const next=page(row(after));context.retainDeliveryEvidence(page(row(before)),next);assert.equal(next.messages[0].delivery_state,expected);
}
for(const changes of [{source_channel_id:'b'},{source_conversation_id:'other'},{source_lead_id:2},{external_id:'connect:other'},{id:'other'},{direction:'incoming'},{source:'kommo'},{provider_message_id:'different'}]){
 const next=page(row('pending',changes));context.retainDeliveryEvidence(page(row('read',{provider_message_id:'provider'})),next);assert.equal(next.messages[0].delivery_state,'pending');
}
const next=page(row('pending'),{channel_id:'b'});context.retainDeliveryEvidence(page(row('read')),next);assert.equal(next.messages[0].delivery_state,'pending');
assert.match(source,/retainDeliveryEvidence\(currentPage,page\);currentPage=page/);
console.log('Monotonic delivery evidence tests passed');

assert.match(source.slice(source.indexOf('  function updateDeliveryEvidence('),source.indexOf('  async function refreshAudioEvidence(')),/retainDeliveryEvidence\(currentPage,latest\)/);

// Compact 1/2/2 receipts are rendered only from explicit normalized evidence.
context.esc=String;context.deliveryCopy=state=>({sent:'Sent',delivered:'Delivered',read:'Read'}[state]||state);
const markupStart=source.indexOf('  function deliveryMarkup('),markupEnd=source.indexOf('  const completionText=',markupStart);
vm.runInContext(source.slice(markupStart,markupEnd),context);
for(const [state,count,width] of [['sent',1,14],['delivered',2,19],['read',2,19]]){
 const html=context.deliveryMarkup(row(state));
 assert.equal((html.match(/<path /g)||[]).length,count);
 assert.ok(html.includes('width="'+width+'" height="14"'));
 assert.ok(html.includes('aria-label="'+context.deliveryCopy(state)+'"'));
}
for(const state of ['pending','retrying','unknown','failed',undefined]){
 assert.ok(!context.deliveryMarkup(row(state)).includes('<svg'));
}
assert.equal(context.deliveryMarkup(row('read',{direction:'incoming'})),'');
console.log('Compact exact-state receipt markup passed');
