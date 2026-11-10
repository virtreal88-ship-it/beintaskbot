const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname,'../docs/tenant-workflow.js'),'utf8');
const fn = source.match(/function combinedPipelines\(\) \{[\s\S]*?\n  \}/)[0];
const context = {
  config:{pipelines:[{pipeline_id:'10',active:false,owner_telegram_id:20}],stages:[{pipeline_id:'10',stage_id:'100',name:'Closed',settings:{visible:false}}]},
  catalog:{pipelines:[{pipeline_id:'10',name:'Sales',stages:[{stage_id:'100',name:'Open'},{stage_id:'101',name:'New'}]},{pipeline_id:'11',name:'Other',stages:[]}]},
  session:{member:{onboarding:{pipeline:{selected:[{id:10,stage_ids:[100]}]}}}},
};
vm.createContext(context);vm.runInContext(fn+';result=combinedPipelines()',context);
assert.equal(context.result[0].active,false,'Saved inactive pipeline must override onboarding');
assert.equal(context.result[0].owner_telegram_id,20);
assert.equal(context.result[0].stages[0].settings.visible,false,'Saved hidden stage must stay hidden');
assert.equal(context.result[1].active,false,'New catalog pipelines must not automatically grant access');
console.log('PASS: workflow UI preserves explicit visibility and assignments when importing Kommo choices.');

assert.ok(source.includes("member.role === 'manager'"));
assert.ok(source.includes('Şirkət → Əməkdaşlar'));
assert.ok(source.includes('expected_pipeline_ids:prior.pipeline_ids ?? null'));
assert.ok(source.includes('member_pipeline_changes:memberPipelineChanges'));
assert.ok(source.includes('delete nextRules.members'));
for (const duplicate of ['data-role', 'data-permissions', 'memberEditor(', 'readMember(', 'notificationEditor(', 'readNotificationPreferences(', 'members:nextMembers']) {
  assert.ok(!source.includes(duplicate), `Duplicated employee control remains: ${duplicate}`);
}
