/* Synthetic snapshot only. No live tokens, backend, credentials or sends. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const template=fs.readFileSync(path.resolve(__dirname,'../../docs/public-case.html'),'utf8');
const api=fs.readFileSync(path.resolve(__dirname,'../tenant_public_case_api.py'),'utf8');
const csp=api.match(/'Content-Security-Policy':"([^"]+)"/)[1];
const videoFixture=Buffer.from('GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAJcEU2bdLpNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHWTbuMU6uEElTDZ1OsggEjTbuMU6uEHFO7a1OsggJG7AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsCrXsYMPQkBNgIxMYXZmNjEuNy4xMDNXQYxMYXZmNjEuNy4xMDNEiYhAaQAAAAAAABZUrmvIrgEAAAAAAAA/14EBc8WIoXRKz8e/vQycgQAitZyDdW5kiIEAhoVWX1ZQOIOBASPjg4QCYloA4JCwgRC6gRCagQJVsIRVuYEBElTDZ/tzc59jwIBnyJlFo4dFTkNPREVSRIeMTGF2ZjYxLjcuMTAzc3PWY8CLY8WIoXRKz8e/vQxnyKFFo4dFTkNPREVSRIeUTGF2YzYxLjE5LjEwMSBsaWJ2cHhnyKFFo4hEVVJBVElPTkSHkzAwOjAwOjAwLjIwMDAwMDAwMAAfQ7Z1QJ3ngQCjvIEAAICwAgCdASoQABAAAEcIhYWIhYSIAgICdaoD+AIM/SgA/v9NEv/8WFfxYV/FhX/xYV/8/M7txfzmAKOVgQAoALEBAAUQrAAYABhYL/QACAAAo5WBAFAAsQEABRCsABgAGFgv9AAIAACjlYEAeACxAQAFEKwAGAAYWC/0AAgAAKOVgQCgALEBAAUQrAAYABhYL/QACAAAHFO7a5G7j7OBALeK94EB8YIBo/CBAw==','base64');
const audioFixture=Buffer.alloc(844);audioFixture.write('RIFF');audioFixture.writeUInt32LE(836,4);audioFixture.write('WAVEfmt ',8);audioFixture.writeUInt32LE(16,16);audioFixture.writeUInt16LE(1,20);audioFixture.writeUInt16LE(1,22);audioFixture.writeUInt32LE(8000,24);audioFixture.writeUInt32LE(16000,28);audioFixture.writeUInt16LE(2,32);audioFixture.writeUInt16LE(16,34);audioFixture.write('data',36);audioFixture.writeUInt32LE(800,40);
const snapshot={snapshot_at:'2026-11-01T10:00:00Z',snapshot_revision:2,is_partial:true,selected_media_ids:['image','document','audio','video'],public_messages:[
 {id:'one',speaker:'Müştəri',text:'Salam!\nŞəkilə baxa bilərsiniz?',at:'2026-11-01T10:00:00Z'},
 {id:'two',speaker:'Texniki komanda',text:'Salam, baxırıq.',at:'2026-11-01T10:02:00Z'},
 {id:'image',speaker:'Müştəri',type:'image',text:'Şəkil',at:'2026-11-02T11:00:00Z'},
 {id:'document',speaker:'BeinSystems',type:'document',text:'Sənəd',at:'2026-11-02T11:01:00Z'},
 {id:'audio',speaker:'Müştəri',type:'audio',text:'Səs',at:'2026-11-02T11:02:00Z'},
 {id:'video',speaker:'Müştəri',type:'video',text:'Video',at:'2026-11-02T11:03:00Z'},
 {id:'unsafe',speaker:'<img src=x onerror=alert(1)>',text:'<script>alert(1)</script> https://private.invalid '+ 'x'.repeat(300),at:'unknown'}]};
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
 for(const width of [320,390,1280]){const page=await browser.newPage({viewport:{width,height:850}}),calls=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
 let imageAttempts=0;
 await page.route('**/*',async route=>{const req=route.request();calls.push({url:req.url(),method:req.method()});const url=new URL(req.url());
 if(url.pathname==='/public/case/synthetic')return route.fulfill({headers:{'Content-Security-Policy':csp},contentType:'text/html',body:template.replace('__CASE_DATA__',JSON.stringify(snapshot).replace(/</g,'\\u003c'))});
 if(url.pathname.endsWith('/media/image')){imageAttempts++;if(imageAttempts===1)return route.fulfill({status:503,body:''});return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')});}
 if(url.pathname.endsWith('/media/audio'))return route.fulfill({contentType:'audio/wav',body:audioFixture});
 if(url.pathname.endsWith('/media/video'))return route.fulfill({contentType:'video/webm',body:videoFixture});
 if(url.pathname.includes('/media/'))return route.fulfill({contentType:'application/octet-stream',body:'synthetic attachment'});
 throw new Error('Unexpected external request: '+req.url());});
 await page.goto('https://fixture.invalid/public/case/synthetic');
 assert.equal(calls.length,1,'no attachment autoload');assert.equal(await page.locator('.bubble').count(),7);assert.equal(await page.locator('.day').count(),2);assert.equal(await page.locator('.bubble.team').count(),2);assert.equal(await page.locator('.bubble.team .who').nth(1).textContent(),'BeinSystems');assert.equal(await page.locator('.bubble').last().getAttribute('class'),'bubble ');
 assert.equal(await page.locator('.identity').count(),0,'legacy snapshot has no fabricated identity');assert.equal(await page.locator('form,input,textarea').count(),0);
 assert.equal(await page.locator('.bubble img,.bubble script,.bubble a').count(),0,'untrusted text stays text');assert.match(await page.locator('.bubble').last().textContent(),/<script>/);
 assert.equal(await page.locator('time').first().getAttribute('datetime'),'2026-11-01T10:00:00.000Z');
 const boxes=await page.locator('.bubble').evaluateAll(nodes=>nodes.slice(0,2).map(n=>{const r=n.getBoundingClientRect();return {left:r.left,right:r.right,bg:getComputedStyle(n).backgroundColor}}));assert.ok(boxes[1].left>boxes[0].left);assert.ok(boxes[1].right>boxes[0].right);assert.notEqual(boxes[0].bg,boxes[1].bg);
 await page.getByRole('button',{name:'Şəkli aç',exact:true}).click();await page.getByRole('button',{name:'Yenidən yoxla',exact:true}).waitFor();await page.getByRole('button',{name:'Yenidən yoxla',exact:true}).click();
 const zoom=page.getByRole('button',{name:'Şəkli böyüt',exact:true});await zoom.waitFor();await page.waitForFunction(()=>document.querySelector('img.media')?.naturalWidth>0);await zoom.focus();await page.keyboard.press('Enter');assert.equal(await page.locator('.media-box button[aria-expanded=true]').count(),1);await page.getByRole('button',{name:'Şəkli kiçilt',exact:true}).click();assert.equal(await page.locator('.media-box button[aria-expanded=false]').count(),1);assert.equal(imageAttempts,2,'zoom reuses loaded media');
 await page.getByRole('button',{name:'Sənədi aç',exact:true}).click();await page.locator('a.file').waitFor();assert.match(await page.locator('a.file').getAttribute('href'),/^blob:/);assert.equal(await page.locator('a.file').getAttribute('download'),'attachment');
 await page.getByRole('button',{name:'Səsə qulaq as',exact:true}).click();await page.locator('audio[controls][preload=none]').waitFor();assert.equal(await page.locator('audio[controls][preload=none]').count(),1);
 await page.getByRole('button',{name:'Videonu aç',exact:true}).click();await page.locator('video[controls][preload=none]').waitFor();assert.equal(await page.locator('video[controls][preload=none]').count(),1);await page.locator('audio,video').evaluateAll(nodes=>nodes.forEach(n=>{n.preload='auto';n.load()}));await page.waitForFunction(()=>{const media=[...document.querySelectorAll('audio,video')];return media.length===2&&media.every(n=>n.readyState>=2&&!n.error)});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no mobile horizontal overflow');assert.ok(calls.every(c=>c.method==='GET'));assert.deepEqual(errors,[]);
 if(process.env.PUBLIC_CHAT_SCREENSHOT_DIR){await page.screenshot({path:path.join(process.env.PUBLIC_CHAT_SCREENSHOT_DIR,`public-chat-${width}.png`),fullPage:true});}
 await page.close();
 }
 for(const width of [320,390,1280]){const page=await browser.newPage({viewport:{width,height:850}}),calls=[];const identity={version:1,customer_name:'<img src=x onerror=alert(1)>',phone_status:'verified',verified_phone:'+15550001111',company_name:'Client <b>company</b>',operator_name:'Employee <script>bad()</script>',last_message_at:'2026-11-02T11:03:00Z'};
 await page.route('**/*',route=>{calls.push(route.request().url());assert.equal(new URL(route.request().url()).pathname,'/public/case/header');return route.fulfill({headers:{'Content-Security-Policy':csp},contentType:'text/html',body:template.replace('__CASE_DATA__',JSON.stringify({...snapshot,public_identity:identity}).replace(/</g,'\\u003c'))});});
 await page.goto('https://fixture.invalid/public/case/header');assert.equal(calls.length,1);assert.equal(await page.locator('#description').isVisible(),false);assert.equal(await page.locator('#snapshot').isVisible(),false);assert.equal(await page.locator('.identity span').count(),5);assert.equal(await page.locator('.identity img,.identity script,.identity b').count(),0);for(const key of ['customer_name','verified_phone','company_name','operator_name'])assert.ok((await page.locator('.identity').textContent()).includes(identity[key]));assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 if(process.env.PUBLIC_CHAT_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.PUBLIC_CHAT_SCREENSHOT_DIR,`public-header-${width}.png`),fullPage:true});await page.close();}
 for(const width of [320,390,1280]){
 const page=await browser.newPage({viewport:{width,height:850}});
 const named={public_messages:[
  {id:'a',direction:'incoming',speaker:'Synthetic Customer',text:'Customer on the left'},
  {id:'b',direction:'outgoing',speaker:'Actual Employee',text:'Employee on the right'},
  {id:'c',direction:'outgoing',speaker:'Əməkdaş',text:'No invented name'},
  {id:'d',direction:'incoming',speaker:'BeinSystems',text:'Direction beats display name'},
  {id:'e',speaker:'Əməkdaş',text:'Legacy known staff role'},
  {id:'f',direction:'unknown',speaker:'BeinSystems',text:'Unknown is not inferred'}]};
 await page.route('**/*',route=>route.fulfill({headers:{'Content-Security-Policy':csp},contentType:'text/html',body:template.replace('__CASE_DATA__',JSON.stringify(named))}));
 await page.goto('https://fixture.invalid/public/case/named');
 assert.equal(await page.locator('.bubble.team').count(),3);
 const positions=await page.locator('.bubble').evaluateAll(nodes=>nodes.map(n=>({left:n.getBoundingClientRect().left,right:n.getBoundingClientRect().right,side:getComputedStyle(n).alignSelf})));
 assert.deepEqual(positions.map(p=>p.side),['flex-start','flex-end','flex-end','flex-start','flex-end','flex-start']);
 assert.ok(positions[1].right>positions[0].right);
 assert.equal(await page.locator('.bubble.team .who').first().textContent(),'Actual Employee');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 if(process.env.PUBLIC_CHAT_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.PUBLIC_CHAT_SCREENSHOT_DIR,`public-named-${width}.png`),fullPage:true});
 await page.close();
 }
 const page=await browser.newPage();await page.setContent(template.replace('__CASE_DATA__','{}'));assert.match(await page.locator('.empty').textContent(),/mesaj yoxdur/);await page.close();console.log('Public case chat: desktop/mobile, snapshot safety, empty state, media retry and keyboard zoom passed.');
}finally{await browser.close()}})().catch(error=>{console.error(error);process.exit(1)});
