import assert from 'node:assert/strict';
import {dealColumns} from '../ui/src/deal-columns.js';
const catalog = [{pipeline_id:1,name:'One',stages:[{stage_id:20,name:'New'},{stage_id:10,name:'Empty'}]}, {pipeline_id:2,name:'Two',stages:[{stage_id:20,name:'New'}]}];
const rows = [{kommo_lead_id:1,pipeline_id:1,status_id:20,stage_name:'New'}];
const result = dealColumns(rows,catalog);
assert.deepEqual(result.map(x=>x.key),['1:20','1:10','2:20']);
assert.equal(result[1].rows.length,0);
assert.equal(result[2].rows.length,0);
assert.equal(result[0].rows[0], rows[0]);
assert.deepEqual(dealColumns(rows,catalog,['1:20']).map(x=>x.key),['1:10','2:20']);
assert.equal(dealColumns([],catalog).length,3);
assert.equal(dealColumns([],catalog,['1:20','1:10','2:20']).length,0);
assert.equal(catalog[0].stages[0].rows,undefined);
assert.equal(dealColumns([{pipeline_id:9,status_id:2}],catalog).at(-1).key,'9:2');
console.log('PASS all stages, order, duplicate names, personal hidden keys, own-created fallback');

assert.notEqual(result[0].appearance.color,result[1].appearance.color);
assert.deepEqual(dealColumns(rows,catalog,['1:20'])[0].appearance,result[1].appearance);
assert.deepEqual(dealColumns([],catalog)[0].appearance,result[0].appearance);
import {stageStyle} from '../ui/src/deal-stage-style.js';
assert.equal(stageStyle('#123456').color,'#123456');
assert.equal(stageStyle('url(evil)').color,stageStyle('').color);
function luminance(css){const rgb=css.match(/\d+/g).map(Number).map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4});return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722}
for(const color of ['#ffffff','#000000','#ff0000','#22c55e','#0000ff','']){const s=stageStyle(color);assert((luminance(s.background)+.05)/(luminance(s.ink)+.05)>4.5)}
console.log('PASS stable distinct stage palette, exact colors, CSS rejection and text contrast');
