const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../docs/index.html'),'utf8');
const script=fs.readFileSync(path.join(__dirname,'../docs/task-date-fields.js'),'utf8');
new Function(script);
const ctx={};vm.runInNewContext(script.slice(0,script.indexOf('(() => {')),ctx);
assert.equal(ctx.taskDateDisplay('2026-10-07T16:45'),'07/10/2026 16:45');
assert.equal(ctx.taskDateIso('07/10/2026 16:45'),'2026-10-07T16:45');
assert.equal(ctx.taskDateIso('29/02/2028 00:00'),'2028-02-29T00:00');
for(const invalid of ['29/02/2026 16:45','31/04/2026 16:45','10/07/2026 24:00','2026-10-07 16:45','07/13/2026 16:45']) assert.equal(ctx.taskDateIso(invalid),null);
assert.equal(ctx.taskDateIso(''),'');
assert.match(html,/<div id="task-edit-modal">/);
assert(!html.includes('if(event.target===this) closeTaskEditModal()'));
const start=html.indexOf('function openLinearEditModal(');
assert(start>=0);
const edit=html.slice(start,html.indexOf('\nasync function loadLinearAccounts',start));
assert(!edit.includes("modal.addEventListener('click'"),'Linear edit must not dismiss on backdrop');
assert(edit.includes('onclick="closeLinearModal()"'));
assert(html.includes('/assets/task-date-fields.js'));
class Field {
  constructor(){this.handlers={};this.style={};this.className='';this.required=false;this.children=[];this.type='text';}
  get value(){return this._value||'';} set value(value){this._value=String(value);}
  before(){} append(...children){this.children.push(...children);}setAttribute(){}
  addEventListener(name,fn){this.handlers[name]=fn;} dispatchEvent(event){this.handlers[event.type]?.(event);}
  setCustomValidity(message){this.error=message;}reportValidity(){return !this.error;}
  focus(){}showPicker(){this.picked=true;}
}
const native=new Field();native.type='datetime-local';native.value='2026-10-07T16:45';
const elements=[];
const dom={HTMLInputElement:Field,Event:class {constructor(type){this.type=type;}},
           MutationObserver:class {observe(){}},document:{body:{},querySelectorAll:()=>[native],createElement:()=>{const field=new Field();elements.push(field);return field;},getElementById:()=>native}};
vm.runInNewContext(script,dom);
assert.equal(native.taskDateText.value,'07/10/2026 16:45');
native.value='2026-11-09T10:05';assert.equal(native.taskDateText.value,'09/11/2026 10:05','programmatic population must sync');
native.taskDateText.value='31/02/2026 10:05';native.taskDateText.dispatchEvent({type:'input'});
assert.equal(native.value,'');assert.equal(dom.validateTaskDateField('date'),false);
native.taskDateText.value='11/12/2026 10:05';native.taskDateText.dispatchEvent({type:'input'});
assert.equal(native.value,'2026-12-11T10:05');assert.equal(dom.validateTaskDateField('date'),true);
elements[2].onclick();assert.equal(native.picked,true);
console.log('PASS: DD/MM/YYYY roundtrip, invalid dates, calendar ISO contract, explicit modal closure.');
