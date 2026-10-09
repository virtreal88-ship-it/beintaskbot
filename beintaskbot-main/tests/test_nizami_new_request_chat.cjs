const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('docs/index.html','utf8');
const names=['isNizamiHiddenChatStage','chatBelongsToViewer','applyInboxPulseRow'];
const code=names.map(name=>source.match(new RegExp(`function ${name}\\([^]*?\\n\\}`))[0]).join('\n');
const row={id:7,pipeline_id:8329347,nizami_queue_kind:'new_request',chat_channel:'instagram'};
const ctx={CURRENT_PIPELINE_ID:14243944,NIZAMI_PIPELINE_ID:14243944,SOVDELESMELER_PIPELINE_ID:8329347,
    isAdmin:true,adminChatDealsLoaded:true,IS_RUFAT:false,samilDeals:[{id:7,pipeline_id:14243944}],
    isNizamiQueueDeal:()=>true,isSuccessfulChatDeal:()=>false,
    showLivePreviewInOpenChat:()=>{throw Error('Hidden-stage preview must not open');}};
vm.createContext(ctx);vm.runInContext(code,ctx);
assert(ctx.isNizamiHiddenChatStage(row));assert(!ctx.chatBelongsToViewer(row));
for(const stage_name of ['Yeni müraciət','YENİ MÜRACİƏTLƏR','yeni müraciətlər']) {
    assert(ctx.isNizamiHiddenChatStage({pipeline_id:8329347,stage_name}));
}
assert(!ctx.isNizamiHiddenChatStage({pipeline_id:14243944,stage_name:'yeni sorgu'}));
assert(!ctx.isNizamiHiddenChatStage({pipeline_id:8329347,nizami_queue_kind:'hot',stage_name:'Nömrə alındı'}));
assert(ctx.chatBelongsToViewer({pipeline_id:8329347,nizami_queue_kind:'hot',chat_channel:'whatsapp'}));
assert(ctx.applyInboxPulseRow(row));assert.equal(ctx.samilDeals.length,1);
assert.equal(ctx.samilDeals[0].nizami_queue_kind,'new_request');
assert(!ctx.chatBelongsToViewer(ctx.samilDeals[0]));
assert(!ctx.applyInboxPulseRow({...row,id:9}));assert.equal(ctx.samilDeals.length,1);
ctx.CURRENT_PIPELINE_ID=999;
assert(!ctx.isNizamiHiddenChatStage(row),'Other employee remains unchanged');
assert(ctx.chatBelongsToViewer(row),'Other administrator remains unchanged');
console.log('PASS: Nizami hides only shared new-request chats, preserves hot/own funnel and workspace deals, suppresses live preview.');
