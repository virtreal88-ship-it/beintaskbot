/* Late responses must never restore an old tenant's approvals. */
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict'), path = require('node:path');
class Element {
  constructor(tag='div') {this.tag=tag;this.children=[];this.style={};this.hidden=false;}
  append(...items) {this.children.push(...items);}
  replaceChildren() {this.children=[];}
  set innerHTML(value) {
    this.html=value;
    if(value.includes('approval-list')) this.parts={button:new Element('button'),'.approval-list':new Element(),'.approval-notice':new Element(),'.approval-more':new Element('button')};
  }
  querySelector(key) {return this.parts[key];}
  querySelectorAll(tag) {return this.children.filter(e=>e.tag===tag);}
}
const host=new Element(), listeners={}, pending=[];
let resolveMe;
const document={getElementById:()=>host,createElement:tag=>new Element(tag),addEventListener:(name,fn)=>listeners[name]=fn};
function fetch(url) {
  if(url.endsWith('/me')) return new Promise(resolve=>{resolveMe=resolve;});
  return new Promise(resolve=>pending.push(resolve));
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const profile=role=>listeners['tenant-profile']({detail:{member:{role},capabilities:{modules:{tasks:true}}}});
const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-task-approvals.js'),'utf8');
vm.runInNewContext(source,{document,fetch,Date});
(async()=>{
  const panel=host.children[0],list=panel.querySelector('.approval-list');
  profile('owner');assert.equal(pending.length,1);
  // The initial profile lookup started earlier than the explicit profile event.
  resolveMe({json:async()=>({success:true,member:{role:'worker'},capabilities:{modules:{tasks:true}}})});
  await tick();assert.equal(panel.hidden,false);
  profile('worker');assert.equal(panel.hidden,true);
  pending.shift()({ok:true,json:async()=>({success:true,approvals:[{creator_id:1,request_id:'old',task:{text:'Old tenant'}}]})});
  await tick();assert.equal(list.children.length,0);
  profile('owner');assert.equal(panel.hidden,false);
  profile('owner');assert.equal(list.children.length,0);
  pending.shift()({ok:true,json:async()=>({success:true,approvals:[{creator_id:1,request_id:'late',task:{text:'Wrong company'}}]})});
  await tick();assert.equal(list.children.length,0);
  pending.shift()({ok:true,json:async()=>({success:true,approvals:[{creator_id:2,request_id:'current',task:{text:'Current company'}}],has_more:false})});
  await tick();assert.equal(list.children.length,1);assert.ok(list.children[0].html.includes('Current company'));
  console.log('PASS: stale profile lookup and late tenant queue responses are ignored.');
})().catch(error=>{console.error(error);process.exitCode=1;});
