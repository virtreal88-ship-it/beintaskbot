/* Synthetic, frozen public identities only. No network or real customer data. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const template=fs.readFileSync(path.resolve(__dirname,'../../docs/public-case.html'),'utf8');
const identity={version:1,customer_name:'<img src=x onerror=alert(1)>',company_name:'Company <b>name</b>',operator_name:'Employee <script>bad()</script>',phone_status:'verified',verified_phone:'+15550001111',last_message_at:'2026-11-02T11:03:00Z'};
function render(public_identity){return new JSDOM(template.replace('__CASE_DATA__',JSON.stringify({public_identity,snapshot_at:'2026-12-30T00:00:00Z',snapshot_revision:99,customer_name:'DO NOT INFER',public_messages:[{speaker:'DO NOT INFER',text:'Message',at:'2026-12-29T00:00:00Z'}]}).replace(/</g,'\\u003c')),{url:'https://fixture.invalid/public/case/header',runScripts:'dangerously'});}
for(const fixture of [identity,{version:1,customer_name:' ',company_name:'',phone_status:'unknown',verified_phone:'UNVERIFIED',operator_name:null,last_message_at:'invalid'}]){
 const dom=render(fixture),d=dom.window.document,head=d.querySelector('.head'),box=d.querySelector('.identity-v1');
 assert.equal(head.querySelectorAll('h1').length,1);assert.equal(box.querySelectorAll('h2').length,1);assert.equal(box.getAttribute('aria-labelledby'),box.querySelector('h2').id);
 assert.deepEqual([...box.querySelectorAll('dt')].map(n=>n.textContent),['Müştərinin şirkəti','Təsdiqlənmiş telefon','Sorğunu göndərən əməkdaş','Fraqmentin son mesajı']);
 assert.equal(box.querySelectorAll('dd').length,4);assert.equal(box.querySelectorAll('a,button,input,img,script,b').length,0);assert.equal(d.querySelector('#description').hidden,true);assert.equal(d.querySelector('#snapshot').hidden,true);assert.doesNotMatch(box.textContent,/UNVERIFIED|Invalid Date|DO NOT INFER|2026-12/);
 if(fixture===identity){for(const key of ['customer_name','company_name','operator_name','verified_phone'])assert.ok(box.textContent.includes(identity[key]));assert.equal(box.querySelector('time').dateTime,'2026-11-02T11:03:00.000Z');}else{assert.equal(box.querySelectorAll('.is-missing').length,5);assert.equal(box.querySelector('time'),null);}
 dom.window.close();
}
for(const fixture of [undefined,{customer_name:'Legacy customer',phone_status:'verified',verified_phone:'+15555555555'},{version:2,customer_name:'Legacy customer'}]){const dom=render(fixture),d=dom.window.document;assert.equal(d.querySelector('.identity-v1'),null);assert.equal(d.querySelector('.has-public-identity'),null);assert.equal(d.querySelector('#description').hidden,false);assert.equal(d.querySelector('#snapshot').hidden,false);if(fixture)assert.ok(d.querySelector('.identity').textContent.includes('Legacy customer'));dom.window.close();}
console.log('PASS: public header hierarchy, semantic labels, frozen date, escaping, missing fields, unverified phone suppression and legacy fallback.');
