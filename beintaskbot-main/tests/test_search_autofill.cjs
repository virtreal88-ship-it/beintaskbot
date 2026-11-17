const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require(process.env.SUPPORT_CASES_JSDOM||'jsdom');
(async()=>{
 const dom=new JSDOM(fs.readFileSync('docs/tenant-app.html','utf8'),{url:'https://example.test/app',runScripts:'outside-only'}),w=dom.window,d=w.document;
 w.eval(fs.readFileSync('docs/tenant-search-inputs.js','utf8'));
 for(const id of ['taskSearch','customerSearch','dealSearch','companySearch','aiTriggerSearch']){
  const el=d.getElementById(id);assert(el,id);assert.equal(el.autocomplete,'off');assert(el.readOnly);el.focus();assert(!el.readOnly);el.value='user query';el.blur();assert(el.readOnly);assert.equal(el.value,'user query');
 }
 const host=d.createElement('section');d.body.append(host);
 host.innerHTML='<input id="query" type="search" value="restored"><input id="locked" type="search" readonly><input id="min" type="search" minlength="4"><form><input id="required" data-search-input type="tel" required></form><form><input id="login" autocomplete="username"><input id="secret" type="password" autocomplete="current-password"><input id="loginSearch" type="search"></form><input id="text"><input id="email" type="email">';
 // Immediate focus before observer still prepares synchronously.
 const query=d.getElementById('query');query.focus();assert(!query.readOnly);assert.equal(query.autocomplete,'off');assert.equal(query.value,'restored');query.blur();assert(query.readOnly);
 await new Promise(r=>w.setTimeout(r,0));
 for(const id of ['login','secret','loginSearch','text','email'])assert.equal(d.getElementById(id).getAttribute('data-lpignore'),null);
 assert.equal(d.getElementById('login').autocomplete,'username');assert.equal(d.getElementById('secret').autocomplete,'current-password');
 const locked=d.getElementById('locked');locked.focus();assert(locked.readOnly);locked.blur();
 for(const id of ['min','required'])assert(!d.getElementById(id).readOnly,'HTML validation must stay enabled');
 assert(!d.getElementById('required').checkValidity());
 const owned=d.getElementById('dealSearch');assert.equal(owned.form.id,'dealSearchForm');assert.equal(owned.name,'deal_query');
 host.innerHTML='<input type="search" id="replacement">';await new Promise(r=>w.setTimeout(r,0));assert(d.getElementById('replacement').readOnly);
 assert.match(fs.readFileSync('docs/tenant-instructions.html','utf8'),/src="\/assets\/tenant-search-inputs.js"/);
 dom.window.close();console.log('PASS app search inventory, dynamic focus/remount, login exclusions, original readonly, HTML validation, values/forms preserved');
})().catch(e=>{console.error(e);process.exitCode=1;});
