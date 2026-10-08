const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../docs/index.html'),'utf8');
const names=['terminalStageLabel','isSuccessfulChatDeal','isDeclinedChatDeal','chatsInboxDeals','applyInboxPulseRow'];
const code=names.map(name=>source.match(new RegExp(`function ${name}\\([^]*?\\n\\}`))[0]).join('\n');
const won={id:1,status_id:142,stage_key:'ugurlu',needs_reply:true,last_client_message:'New'};
const lost={id:2,status_id:143,stage_key:'imtina',needs_reply:true};const active={id:3,status_id:555};
const context={samilDeals:[won,lost,active],_chatsListFilter:'all',_chatsAssigneeFilter:'all',_chatsStageFilters:new Set(),isAdmin:false,
 document:{getElementById:()=>({value:''})},chatInboxSourceDeals:()=>context.samilDeals,dealLastClientMessage:()=>'',
 chatBelongsToViewer:()=>true,chatsIsUnanswered:()=>true,chatsFollowupState:()=>null,isChatPinned:()=>false,chatsListAt:()=>0,
 dealByWaPhone:()=>active,viewerWaLine:()=>'',showLivePreviewInOpenChat:()=>{throw Error('Successful message must not be displayed');}};
vm.createContext(context);vm.runInContext(code,context);
assert.equal(context.terminalStageLabel(142,'','Успешно реализовано'),'Uğurla tamamlandı');
assert.equal(context.terminalStageLabel(143,'','Закрыто и не реализовано'),'İmtina olundu');
for(const isAdmin of [true,false]){context.isAdmin=isAdmin;for(const filter of ['all','unanswered','visible'])assert(!context.chatsInboxDeals(filter).some(row=>row.id===1));}
assert(context.chatsInboxDeals('unanswered').some(row=>row.id===2),'Declined reentry stays unchanged');
assert(!context.applyInboxPulseRow({id:9,status_id:142,phone:'Same phone',last_client_message:'Won'}));
assert.equal(active.status_id,555,'Won row must not attach to another deal via phone');
assert(!context.samilDeals.some(row=>row.id===9));
assert(!context.applyInboxPulseRow({id:1,last_client_message:'Won incoming with missing stage'}));
console.log('PASS: successful deals stay out of all chat tabs, declined behavior is preserved and terminal names are Azerbaijani.');
