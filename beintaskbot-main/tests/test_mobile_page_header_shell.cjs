const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.join(__dirname,'..'),read=p=>fs.readFileSync(path.join(root,p),'utf8');
(async()=>{
const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,d=w.document;
w.matchMedia=q=>({matches:q.includes('max-width'),addEventListener(){}});w.ResizeObserver=class{observe(){}};w.tenantI18n={language:'ru',t:k=>k};
const shell=read('ui/src/app-shell.js').replace("import themeCss from './app-theme.css?inline'","const themeCss=''").replace("import { installMobilePageHeader } from './mobile-page-header.js'",read('ui/src/mobile-page-header.js').replace('export function','function'));
w.eval(shell);w.eval(read('docs/tenant-mobile-crm-toolbar.js'));
d.querySelectorAll('main>.view').forEach(n=>n.classList.toggle('active',n.id==='view-tasks'));
await new Promise(r=>setTimeout(r,100));
assert.equal(d.querySelector('.app-mobile-title-row h1').textContent,'Tapşırıqlar');
assert.equal(d.querySelectorAll('#view-tasks .crm-mobile-toolbar>button').length,3);
assert.deepEqual([...d.querySelectorAll('.app-nav-group[data-group=crm] [data-view]')].map(n=>n.dataset.view).filter(v=>['tasks','deals','customers','companies'].includes(v)),['tasks','deals','customers','companies']);
assert(d.querySelector('#view-tasks .app-mobile-title-row').compareDocumentPosition(d.querySelector('#view-tasks .crm-mobile-toolbar'))&w.Node.DOCUMENT_POSITION_FOLLOWING);
w.close();console.log('PASS combined header/toolbar shell and CRM order');
})().catch(e=>{console.error(e);process.exit(1)});
