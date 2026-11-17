import assert from 'node:assert/strict';
import {moveKommoDeal,verifyKommoDealMove} from '../ui/src/kommo-deal-move.js';
const storage=new Map();globalThis.sessionStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
const row={source_revision:'a'.repeat(64),kommo_lead_id:7,pipeline_id:10,status_id:100,source_updated_at:'2026-11-14T00:00:00Z',raw:{updated_at:1794614400}},context={tenant_id:'a',user_id:'7'};
let requests=[];await assert.rejects(moveKommoDeal({...row,source_revision:null},101,context),/Sync and refresh/);assert.equal(storage.size,0);globalThis.fetch=async(url,options={})=>{requests.push({url,options});throw Error('lost response')};
await assert.rejects(moveKommoDeal(row,101,context),/Result unknown/);assert.equal(requests[0].options.method,'POST');assert.equal(storage.size,1);
await assert.rejects(moveKommoDeal(row,102,context),/previous stage/);assert.equal(requests.length,1);
globalThis.fetch=async(url,options={})=>{requests.push({url,options});return {ok:true,json:async()=>({source_revision:'b'.repeat(64),success:true,state:'confirmed',lead_id:'7',pipeline_id:10,status_id:101,updated_at:1794614401})}};
const result=await moveKommoDeal(row,101,context);assert.equal(requests[1].options.method,undefined);assert(requests[1].url.includes('request_id='));assert.equal(result.status_id,101);assert.equal(storage.size,0);
globalThis.fetch=async()=>({ok:true,json:async()=>({source_revision:'b'.repeat(64),success:true,state:'confirmed',lead_id:'8',pipeline_id:10,status_id:101,updated_at:1794614401})});await assert.rejects(moveKommoDeal(row,101,context),/receipt/);assert.equal(storage.size,1);
console.log('PASS stage command immutable retry, GET-only verification, destination fence and receipt validation');
storage.clear();requests=[];
globalThis.fetch=async(url,options={})=>{requests.push({url,options});throw Error('lost cross-pipeline response')};
await assert.rejects(moveKommoDeal(row,201,context,20),/Result unknown/);
assert.equal(JSON.parse(requests[0].options.body).target_pipeline_id,'20');
await assert.rejects(moveKommoDeal(row,201,context,30),/previous stage/);assert.equal(requests.length,1);
globalThis.fetch=async(url,options={})=>{requests.push({url,options});return {ok:true,json:async()=>({source_revision:'b'.repeat(64),success:true,state:'confirmed',lead_id:'7',pipeline_id:20,status_id:201,updated_at:1794614401})}};
const moved=await moveKommoDeal(row,201,context,20);assert.equal(requests[1].options.method,undefined);assert.equal(moved.pipeline_id,20);assert.equal(moved.raw.pipeline_id,20);assert.equal(storage.size,0);
// A legacy saved request with no target_pipeline_id keeps its identity and GET route.
const legacyKey='kommo-stage:a:7:7';storage.set(legacyKey,JSON.stringify({expected_pipeline_id:'10',stage_id:'101',request_id:'legacy'}));
globalThis.fetch=async(url,options={})=>{assert(url.includes('request_id=legacy'));assert.equal(options.method,undefined);return {ok:true,json:async()=>({source_revision:'b'.repeat(64),success:true,state:'confirmed',lead_id:'7',pipeline_id:10,status_id:101,updated_at:1794614401})}};
await moveKommoDeal(row,101,context);assert.equal(storage.size,0);
console.log('PASS cross-pipeline tuple fence, GET-only replay, pipeline/raw receipt and legacy command');

// An explicitly uncertain POST is resolved with one GET, never another PATCH.
storage.clear();requests=[];
globalThis.fetch=async(url,options={})=>{requests.push({url,options});return options.method==='POST'
 ?{ok:false,json:async()=>({success:false,error:'board_outcome_unknown',keep_request:true})}
 :{ok:true,json:async()=>({source_revision:'b'.repeat(64),success:true,state:'confirmed',lead_id:'7',pipeline_id:10,status_id:101,updated_at:1794614401})};};
const reconciled=await moveKommoDeal(row,101,context);assert.equal(reconciled.status_id,101);assert.equal(requests.length,2);assert.equal(requests[0].options.method,'POST');assert.equal(requests[1].options.method,undefined);assert(requests[1].url.includes(JSON.parse(requests[0].options.body).request_id));assert.equal(storage.size,0);
storage.clear();requests=[];
globalThis.fetch=async(url,options={})=>{requests.push({url,options});return {ok:false,json:async()=>({success:false,error:'board_outcome_unknown',keep_request:true})}};
await assert.rejects(moveKommoDeal(row,101,context),/Result unknown/);assert.equal(requests.length,2);assert.equal(storage.size,1);
console.log('PASS one bounded GET-only reconciliation; unresolved command retained');

// Background checks must never manufacture a command when storage disappeared.
storage.clear();requests=[];globalThis.fetch=async()=>{throw Error('must not fetch')};
assert.equal(await verifyKommoDealMove(row,101,context),null);assert.equal(storage.size,0);
console.log('PASS verification-only absent request does not post or create a UUID');
