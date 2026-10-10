const assert=require('node:assert/strict');
const {columns,date,escape}=require('../docs/tenant-workspace.js');
const catalog=[{pipeline_id:1,name:'Sales',stages:[{stage_id:10,name:'New'},{stage_id:11,name:'In progress'}]},
  {pipeline_id:2,name:'Service',stages:[{stage_id:10,name:'New'}]}];
const rows=[{pipeline_id:1,status_id:10,kommo_lead_id:1,source_updated_at:'2026-10-09T12:00:00Z'},
  {pipeline_id:1,status_id:10,kommo_lead_id:2,source_updated_at:'2026-10-10T12:00:00Z'},
  {pipeline_id:2,status_id:10,kommo_lead_id:3},
  {pipeline_id:1,status_id:12,stage_name:'Imported',kommo_lead_id:4}];
const output=columns(rows,catalog);
assert.equal(output[0].stages[0].rows[0].kommo_lead_id,2);
assert.equal(output[0].stages[1].rows.length,0);
assert.equal(output[1].stages[0].rows.length,1);
assert.equal(output[0].stages[2].name,'Imported');
assert.equal(catalog[0].stages[0].rows,undefined);
assert.equal(rows[0].kommo_lead_id,1);
assert.equal(columns([],[]).length,0);
assert.equal(date('not-a-date'),'—');
assert.match(date('2026-10-10T12:00:00Z'),/^10\/10\/2026 \d\d:\d\d$/);
assert.equal(escape('<img onerror="x">'),'&lt;img onerror=&quot;x&quot;&gt;');
console.log('PASS: board grouping, ordering, empty stages, isolation and escaping');
