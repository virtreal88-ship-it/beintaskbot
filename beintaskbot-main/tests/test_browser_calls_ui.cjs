/* Real Chromium, Web Locks, getUserMedia-shaped synthetic stream and WebRTC offer.
 * Signaling replies mocked: this is NOT evidence of a live WhatsApp conversation. */
const assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const docs=path.resolve(__dirname,'../docs'),managed=process.env.CALL_TEST_MEDIA==='websocket';
const scripts=['tenant-microphone-lease.js','tenant-browser-calls.js'];
const html=`<!doctype html><meta charset="utf-8"><div style="display:flex;justify-content:flex-end"><span id="operatorBrowserCallHost"></span><button id="more">⋯</button></div>
<script>window.tenantOperator={member:{tenant_id:'tenant',telegram_id:11},currentDialog:{channel_id:'source',conversation_id:'peer',lead_id:41,browser_call_available:true},openDialog:async()=>{window.refreshes=(window.refreshes||0)+1}};
window.streams=[];window.contexts=[];navigator.mediaDevices.getUserMedia=async()=>{if(window.holdMic)await new Promise(r=>window.resumeMic=r);const ctx=new AudioContext(),osc=ctx.createOscillator(),dest=ctx.createMediaStreamDestination();osc.connect(dest);osc.start();window.streams.push(dest.stream);window.contexts.push(ctx);return dest.stream;};</script>
${scripts.map(s=>`<script src="/assets/${s}"></script>`).join('')}`;
async function main(){
 const server=http.createServer((req,res)=>{const file=(req.url||'').split('?')[0].replace('/assets/','');if([...scripts,'browser-call-audio.js'].includes(file)){res.setHeader('content-type','application/javascript');return res.end(fs.readFileSync(path.join(docs,file)));}res.setHeader('content-type','text/html;charset=utf-8');res.end(html);});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;
 const browser=await chromium.launch({headless:true});const context=await browser.newContext();
 let calls=[],posts=[],dropStart=false,deny=false,frames=0;
 if(managed)await context.routeWebSocket('**/api/platform/browser-calls/*/audio?*',ws=>ws.onMessage(data=>{assert(Buffer.isBuffer(data)&&data.length<=6400&&data.length%2===0);frames++;ws.send(data);}));
 await context.route('**/api/platform/browser-calls?*',async r=>{const req=r.request(),url=new URL(req.url());if(deny)return r.fulfill({status:403,json:{error:'access revoked'}});
   if(req.method()==='GET')return r.fulfill({json:{result:{enabled:true,online:true,test_number:'994123456789',sources:[{channel_id:'source',number:'994123456789',online:true,media:managed?'websocket':'webrtc',permanent:managed,retention_hours:managed?720:24}],calls:calls.map(c=>({...c,audio_ready:managed&&c.claimed,request_matches:url.searchParams.get('request_id')===c.nonce}))}}});
   const d=req.postDataJSON();posts.push(d);
   if(d.op==='start'||d.op==='accept'){if(managed){assert.equal(d.media,'websocket');assert.equal(d.sdp_offer,'');}else assert(d.sdp_offer.startsWith('v=0'));const c={...d,id:d.call_id||'call-'+posts.length,nonce:d.request_id,direction:d.op==='start'?'outbound':'inbound',state:'connected',owned:true,claimed:true,recording_state:'pending'};calls=[c];if(dropStart)return r.abort('failed');return r.fulfill({json:{result:c}});}
   if(d.op==='end'){const c=calls.find(c=>c.id===d.call_id||c.nonce===d.request_id);if(c)c.state='ended';return r.fulfill({json:{result:c||{state:'not_started'}}});}
   if(d.op==='reject'){calls=calls.map(c=>({...c,state:'ended'}));return r.fulfill({json:{result:calls[0]}});}
   throw Error('Unexpected action');
 });
 try{
  const p=await context.newPage();await p.goto(origin);await p.waitForResponse(r=>r.url().includes('/browser-calls?')).catch(()=>{});
  const phone=p.getByRole('button',{name:'Звонок WhatsApp',exact:true});await phone.waitFor();
  const positions=await p.evaluate(()=>[document.querySelector('#operatorBrowserCallHost').getBoundingClientRect().right,document.querySelector('#more').getBoundingClientRect().left]);assert(positions[0]<=positions[1]);
  await phone.click();assert(await p.getByText('WhatsApp источника +994123456789.',{exact:false}).isVisible());assert(await p.getByRole('button',{name:'Позвонить',exact:true}).isDisabled());await p.locator('input[type=checkbox]').check();
  await p.evaluate(()=>{const b=document.querySelector('.accept');b.click();b.click();});await p.getByText('Разговор',{exact:true}).waitFor();assert.equal(posts.filter(x=>x.op==='start').length,1);if(managed){for(let i=0;i<40&&!frames;i++)await p.waitForTimeout(100);assert(frames>0,'managed PCM must actually reach the WebSocket mock');assert(await p.getByText('запись хранится 30 дней.',{exact:false}).isVisible());}
  await p.getByRole('button',{name:'Закрыть звонок'}).click();await p.locator('dialog').waitFor({state:'detached'});assert(await p.evaluate(()=>streams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))));
  await p.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await p.waitForTimeout(300);await phone.click();await p.locator('input[type=checkbox]').check();
  await p.evaluate(()=>window.holdMic=true);await p.getByRole('button',{name:'Позвонить',exact:true}).click();await p.waitForFunction(()=>!!window.resumeMic);await p.getByRole('button',{name:'Закрыть звонок'}).click();await p.evaluate(()=>{window.holdMic=false;resumeMic();});await p.waitForTimeout(300);assert.equal(posts.filter(x=>x.op==='start').length,1,'cancel during microphone permission must not dial');
  dropStart=true;await phone.click();await p.locator('input[type=checkbox]').check();await p.getByRole('button',{name:'Позвонить',exact:true}).click();await p.waitForFunction(()=>streams.length===3&&streams[2].getTracks().every(t=>t.readyState==='ended'));await p.getByText('Звонок завершён',{exact:true}).waitFor();assert.equal(calls[0].state,'ended','lost start response must cancel by request nonce');
  await p.getByRole('button',{name:'Закрыть звонок'}).click();dropStart=false;calls=[{id:'incoming-test',channel_id:'source',conversation_id:'peer',lead_id:41,state:'incoming',direction:'inbound',claimed:false}];await p.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await p.getByRole('button',{name:'Отклонить',exact:true}).waitFor();assert.equal(posts.filter(x=>x.op==='accept').length,0,'never auto-answer');await p.getByRole('button',{name:'Отклонить',exact:true}).click();
  calls=[{id:'incoming-next',channel_id:'source',conversation_id:'peer',lead_id:41,state:'incoming',direction:'inbound',claimed:false}];await p.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await p.getByRole('button',{name:'Принять',exact:true}).waitFor();const other=await context.newPage();await other.goto(origin);await other.getByRole('button',{name:'Принять',exact:true}).waitFor();await p.locator('input[type=checkbox]').check();await p.getByRole('button',{name:'Принять',exact:true}).click();await p.getByText('Разговор',{exact:true}).waitFor();
  await other.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await other.locator('dialog').waitFor({state:'detached'});assert.equal(await other.evaluate(()=>streams.length),0,'second tab must neither claim microphone nor keep a claimed incoming modal');await other.close();deny=true;await p.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await p.locator('dialog').waitFor({state:'detached'});assert(await p.evaluate(()=>streams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))));
  await p.setViewportSize({width:390,height:844});const rect=await phone.boundingBox();assert(rect&&rect.x>=0&&rect.x+rect.width<=390);
  console.log('PASS '+(managed?'WebSocket PCM':'WebRTC')+' browser call controls: placement, consent, one start, X, delayed microphone, unknown start, incoming reject/accept, revocation, mobile');
 }finally{await browser.close();await new Promise(r=>server.close(r));}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
