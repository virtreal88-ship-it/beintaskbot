const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../docs/index.html'), 'utf8');
const cardSource = html.slice(html.indexOf('function linearBoardCardHtml('), html.indexOf('function renderLinearBoard('));
const ctx = {isAdmin:false, escapeHtml:value=>String(value || ''),
    linearBoardPriorityStyle:()=>'', linearBoardStatusStyle:()=>'',
    linearBoardPriorityLabel:()=> 'Orta', hasEmployeePermission:()=>true,
    isLinearDoneStatus:()=>false};
vm.createContext(ctx);
vm.runInContext(html.slice(html.indexOf('function formatLinearTimestamp('), html.indexOf('function linearBoardCardHtml(')), ctx);
vm.runInContext(cardSource, ctx);
for (const admin of [false, true]) {
    ctx.isAdmin = admin;
    const card = ctx.linearBoardCardHtml({id:'BS-1335', environment:'DEV', project:'Akul', operator:'admin@nizam'});
    assert.ok(!card.includes('Mühit:'), 'No environment row in kanban card');
    assert.ok(!card.includes('DEV'), 'No environment value in kanban card');
    assert.ok(card.includes('Layihə:</b> Akul'));
    assert.ok(card.includes('Operator:</b> admin@nizam'));
}
console.log('Linear kanban environment checks passed');
