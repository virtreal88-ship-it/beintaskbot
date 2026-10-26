const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const dom=new JSDOM('<main id="instructions"></main>',{url:'https://app.test/app/instructions?lesson=lesson&site_lang=en',runScripts:'outside-only'}),w=dom.window;
w.structuredClone=structuredClone;w.confirm=()=>true;Object.defineProperty(w.crypto,'randomUUID',{value:crypto.randomUUID});
const lesson={id:'lesson',version:4,state:'published',doc:{title:'Inventory',slides:[],subtitles:{slides:[]}}};
const asset={id:'asset',lesson_id:'lesson',version:2,sha256:'a'.repeat(64),active:true,reusable_approved:true,metadata:{task:'inventory save',product:'Akul',product_version:'v1',when_offer:'After text'}};
const calls=[];let ambiguous=false;
w.fetch=async(url,options={})=>{url=new URL(String(url),'https://app.test');assert.equal(url.origin,'https://app.test');url=url.pathname+url.search;assert(url.startsWith('/api/platform/'),'only internal authorized API');const data=options.body?JSON.parse(options.body):null;calls.push({url,data});let result;
 if(url.includes('/me'))return{ok:true,json:async()=>({member:{tenant_id:'tenant',telegram_id:11}})};
 if(url.includes('/ai/agents'))result=[{id:'agent',name:'Test agent'}];
 else if(data){assert.equal(data.expected_tenant_id,'tenant');assert.equal(data.expected_user_id,'11');
  if(data.action==='video_list')result={items:[asset]};
  else if(data.action==='video_find')result={status:ambiguous?'clarify':'selected',items:[asset]};
  else if(data.action==='video_offer'){assert.equal(data.text_answer,'Save the draft.');result={offer_text:'Would you like this video?'};}
  else if(data.action==='video_confirm'){assert(data.simulated_client_yes);result={status:'virtual_sent',customer_sent:false,simulation:true};}
  else throw Error('Unexpected action '+data.action);
 }else result=lesson;
 return{ok:true,json:async()=>({success:true,result})};
};
w.eval(fs.readFileSync('docs/tenant-instructions.js','utf8'));
const settle=()=>new Promise(r=>setImmediate(r));
(async()=>{await settle();await settle();const panel=w.document.querySelector('[data-instruction-videos]');assert(panel,'existing instruction card contains video panel');assert(panel.textContent.includes('customer delivery is disabled'));
 const fields=panel.querySelectorAll('fieldset')[1].querySelectorAll('input');fields[0].value='inventory save';fields[1].value='Akul';fields[2].value='v1';fields[3].value='Save the draft.';
 const buttons=[...panel.querySelectorAll('button')],find=buttons.find(b=>b.textContent==='Find and offer'),yes=buttons.find(b=>b.textContent==='Simulated client yes');assert(yes.disabled);
 find.click();await settle();await settle();assert(!yes.disabled);const offer=calls.find(c=>c.data?.action==='video_offer').data;assert.equal(offer.asset_id,'asset');assert.equal(offer.asset_version,2);
 yes.click();await settle();assert(panel.textContent.includes('virtual_sent'));const confirmation=calls.find(c=>c.data?.action==='video_confirm').data;assert.equal(confirmation.offer_id,offer.offer_id);assert.equal(confirmation.dialog_id,offer.dialog_id);
 ambiguous=true;find.click();await settle();assert(yes.disabled);assert.equal(calls.filter(c=>c.data?.action==='video_offer').length,1);
 w.document.dispatchEvent(new w.CustomEvent('tenant-profile'));assert(!w.document.querySelector('[data-instruction-videos]'));dom.window.close();console.log('PASS existing card video metadata, selection, offer, exact simulated consent, ambiguity, company reset; no outbound');
})().catch(e=>{console.error(e);process.exitCode=1;dom.window.close();});
