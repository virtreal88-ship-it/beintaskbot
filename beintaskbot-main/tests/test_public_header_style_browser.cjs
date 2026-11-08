/* Synthetic public header layout and accessibility regression; no live data. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const template=fs.readFileSync(path.resolve(__dirname,'../../docs/public-case.html'),'utf8');
const api=fs.readFileSync(path.resolve(__dirname,'../tenant_public_case_api.py'),'utf8');
const csp=api.match(/'Content-Security-Policy':"([^"]+)"/)[1];
const normal={version:1,customer_name:'Nümunə müştəri',company_name:'Nümunə şirkət',operator_name:'Nümunə əməkdaş',phone_status:'verified',verified_phone:'+15550001111',last_message_at:'2026-11-02T11:03:00Z'};
function contrast(a,b){const luminance=c=>{const channels=c.match(/[\d.]+/g).slice(0,3).map(x=>{const v=Number(x)/255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4});return channels[0]*.2126+channels[1]*.7152+channels[2]*.0722};const values=[luminance(a),luminance(b)].sort((a,b)=>b-a);return(values[0]+.05)/(values[1]+.05);}
const fixtures={normal,long:{...normal,customer_name:'<img src=x onerror=alert(1)> '+ 'Müştəri'.repeat(35),company_name:'Şirkət'.repeat(50),operator_name:'Əməkdaş'.repeat(40)},missing:{version:1,phone_status:'unknown',verified_phone:'UNVERIFIED',last_message_at:'invalid'}};
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
 for(const width of [320,390,1280])for(const colorScheme of ['light','dark'])for(const [variant,identity] of Object.entries(fixtures)){
  const page=await browser.newPage({viewport:{width,height:850},colorScheme}),calls=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{calls.push(route.request().url());assert.equal(new URL(route.request().url()).pathname,'/public/case/header');return route.fulfill({headers:{'Content-Security-Policy':csp},contentType:'text/html',body:template.replace('__CASE_DATA__',JSON.stringify({public_identity:identity,is_partial:true,public_messages:[{id:'image',type:'image',speaker:'Müştəri',text:'Test message'}],selected_media_ids:['image']}).replace(/</g,'\\u003c'))});});
  await page.goto('https://fixture.invalid/public/case/header');
  assert.equal(await page.getByRole('heading',{level:1}).count(),1);assert.equal(await page.getByRole('heading',{level:2}).textContent(),identity.customer_name||'Göstərilməyib');assert.equal(await page.locator('.identity-details dt').count(),3);
  assert.equal(await page.locator('.head a,.head button,.head img,.head script').count(),0);assert.equal(await page.locator('#partial').isVisible(),true);assert.equal(await page.locator('#description').isVisible(),false);
  assert.equal(await page.locator('.identity time').count(),variant==='missing'?0:1);if(variant!=='missing')assert.equal(await page.locator('.identity time').getAttribute('datetime'),'2026-11-02T11:03:00.000Z');
  for(const zoom of [1,2]){await page.evaluate(zoom=>document.body.style.zoom=String(zoom),zoom);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${width}/${colorScheme}/${variant}/${zoom}: no horizontal overflow`);assert.ok(await page.locator('.identity-detail').evaluateAll(nodes=>nodes.every(n=>n.scrollWidth<=n.clientWidth)), 'metadata wraps within cards');}
  await page.evaluate(()=>document.body.style.zoom='1');
  const styles=await page.evaluate(()=>{const style=s=>getComputedStyle(document.querySelector(s));return{title:parseFloat(style('.identity-name').fontSize),label:parseFloat(style('.identity-label').fontSize),thread:style('.thread').backgroundColor,header:style('.head').backgroundColor,nameColor:style('.identity-name').color}});
  assert.ok(styles.title>styles.label);assert.equal(styles.thread,'rgb(238, 234, 228)','dark preference must not restyle chat');assert.equal(styles.header,colorScheme==='dark'?'rgb(23, 32, 51)':'rgb(255, 255, 255)');assert.ok(contrast(styles.header,styles.nameColor)>=4.5,'customer heading contrast');
  const pairs=await page.locator('.head.has-public-identity h1,.identity-label,.identity-v1 dd,.identity-name,#partial').evaluateAll(nodes=>nodes.map(n=>{let bg=n;while(getComputedStyle(bg).backgroundColor==='rgba(0, 0, 0, 0)')bg=bg.parentElement;return[getComputedStyle(n).color,getComputedStyle(bg).backgroundColor]}));for(const pair of pairs)assert.ok(contrast(...pair)>=4.5,`readable header contrast: ${pair}`);
  await page.keyboard.press('Tab');assert.equal(await page.locator(':focus').textContent(),'Şəkli aç');assert.equal(await page.locator(':focus').evaluate(n=>getComputedStyle(n).outlineWidth),'3px','existing media keyboard focus preserved');
  assert.equal(calls.length,1,'identity adds no requests or automatic media load');assert.deepEqual(errors,[]);
  if(process.env.PUBLIC_CHAT_SCREENSHOT_DIR&&variant==='normal')await page.screenshot({path:path.join(process.env.PUBLIC_CHAT_SCREENSHOT_DIR,`header-style-${width}-${colorScheme}.png`),fullPage:true});await page.close();
 }
 console.log('PASS: header 320/390/1280px, light/dark, long/missing values, 200% zoom, semantics, keyboard focus and no extra network requests.');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
