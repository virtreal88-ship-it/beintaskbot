// Real Chromium against the built Dashboard; only synthetic API fixtures.
// npm install --prefix .support-test-deps playwright; npm run build --prefix ui
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'.support-test-deps/learning-smoke');fs.mkdirSync(out,{recursive:true});
const title={ru:'Улучшения AI',az:'AI təkmilləşdirmələri',en:'AI improvements'};
const html=`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>
:root{--app-surface:#fff;--app-raised:#f4f6f8;--app-ink:#17212b;--app-muted:#586574;--app-line:#dce3e8;--app-accent:#0369a1;--app-accent-soft:#e0f2fe}
:root[data-theme=dark]{--app-surface:#18242b;--app-raised:#24343e;--app-ink:#edf4f6;--app-muted:#aab9c4;--app-line:#43535e}body{margin:0;background:var(--app-surface);color:var(--app-ink)}</style><link rel="stylesheet" href="/docs/tenant-ai-dashboard.css"></head><body><section id="view-ai_dashboard" class="active"><div id="aiDashboard"></div></section><script>const q=new URLSearchParams(location.search);document.documentElement.lang=q.get('lang');document.documentElement.dataset.theme=q.get('theme');window.tenantI18n={language:q.get('lang')};window.tenantProfileSnapshot={member:{tenant_id:'demo',telegram_id:1}};</script><script type="module" src="/docs/tenant-chakra-ui.js"></script></body></html>`;
const server=http.createServer((req,res)=>{if(req.url.startsWith('/app')){res.setHeader('Content-Type','text/html');return res.end(html)}const file=path.resolve(root,'.'+decodeURIComponent(req.url.split('?')[0]));if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end()}res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':'text/css');res.end(fs.readFileSync(file))});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;try{browser=await chromium.launch({headless:true});let cases=0;
for(const lang of ['ru','az','en'])for(const theme of ['light','dark'])for(const width of [1440,390]){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));let writes=0;
 const item={id:'learning:demo',issue_key:'learning:demo',run_id:'run1',category:'context',component:'media',action:'technical',reason:'simulation_media_context_unavailable',count:15,first_seen:'2026-10-23T16:00:00Z',last_seen:'2026-10-23T17:00:00Z',status:'new',agents:{a:'Demo Primary'},channels:{c:'Demo WhatsApp'},examples:[{run_id:'run1',at:'2026-10-23T17:00:00Z',outcome:'unsupported',knowledge_ids:[],expert_ids:[]}],history:[],comparisons:[]};
 await page.route('**/api/**',async route=>{const u=new URL(route.request().url());let result={};if(route.request().method()==='POST'){writes++;assert.ok(u.pathname.endsWith('/review'));const p=route.request().postDataJSON();assert.equal(p.expected_tenant_id,'demo');item.status=p.status;item.history.push({status:p.status,at:'2026-10-23T18:00:00Z'});result={status:p.status}}
 else if(u.pathname.endsWith('/issues'))result={items:!u.searchParams.get('status')||u.searchParams.get('status')===item.status?[item]:[],total:1,next_cursor:null,metrics:{runs:15,replies:0,stops:0,errors:0,knowledge:0,expert:0,confirmed:0,fixed:0,rechecks_without_original_issue:0}};
 else if(u.pathname.endsWith('/runs/run1'))result={id:'run1',outcome:'unsupported',messages:[],events:[],knowledge_refs:[],expert_results:[],rechecks:[],virtual_stop_reason:item.reason};
 else if(u.pathname.endsWith('/runs')||u.pathname.endsWith('/events'))result=[];
 else if(u.pathname.endsWith('/dashboard'))result={agents:[],channels:[],conversations:[],active:{},simulation:{},simulation_config:{enabled:false,runtime_enabled:false}};
 await route.fulfill({json:{success:true,result}})});
 await page.goto(`http://127.0.0.1:${server.address().port}/app?view=ai_dashboard&lang=${lang}&theme=${theme}`);
 await page.getByRole('tab',{name:title[lang],exact:true}).click();const panel=page.getByTestId('ai-improvements');await panel.locator('details').waitFor();
 assert.equal(writes,0);await panel.locator('details summary').click();await panel.locator('select').nth(1).selectOption('in_review');await page.waitForFunction(()=>document.querySelector('[data-testid="ai-improvements"] details')?.textContent.includes('2026-10-23T18:00:00Z'));
 assert.equal(writes,1);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'horizontal overflow');assert.deepEqual(errors,[]);
 await page.screenshot({path:path.join(out,`${lang}-${theme}-${width}.png`),fullPage:true});cases++;await page.close();
}console.log(`${cases} Chromium cases passed: RU/AZ/EN, light/dark, desktop/mobile. Screenshots: ${out}`);
}finally{await browser?.close();server.close()}})().catch(e=>{console.error(e);process.exitCode=1});
