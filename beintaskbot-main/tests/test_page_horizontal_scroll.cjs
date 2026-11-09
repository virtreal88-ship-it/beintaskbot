/* Real browser layout regression, synthetic data only. Requires Chromium. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const assert = require('node:assert/strict');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'docs/tenant-app.html'), 'utf8');
const styles = [...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(x => x[1]).join('\n');
const theme = fs.readFileSync(path.join(root, 'ui/src/app-theme.css'), 'utf8');
const board = fs.readFileSync(path.join(root, 'docs/tenant-linear-board.js'), 'utf8');
const scrollControl = fs.readFileSync(path.join(root, 'docs/tenant-horizontal-scroll.js'), 'utf8');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'page-scroll-'));
const file = path.join(dir, 'fixture.html');
const fixture = `<!doctype html><meta charset="utf-8"><style>${styles}\n${theme}</style>
<html data-chakra-ui="ready" data-app-theme="light"><body data-app-ui="kwid"><div class="shell"><aside><nav class="nav"></nav></aside><main></main></div>
<script>
const requests=[];
const issues=Array.from({length:6},(_,i)=>({id:'i'+i,identifier:'BS-'+i,account:'Fixture account',title:'A long task title '.repeat(15),body:'Details '.repeat(60),state:{id:'s'+i,name:'Status '+i},capabilities:{},priority:2}));
window.fetch=async url=>{requests.push(url);return {ok:true,json:async()=>url.endsWith('/me')?{member:{tenant_id:'fixture',telegram_id:20,role:'owner',permissions:['linear']},capabilities:{modules:{linear:true}}}:{success:true,tenant_id:'fixture',user_id:20,issues,config_version:'1',capabilities:{},pageInfo:{}}}};
</script><script>${board}</script><script>${scrollControl}</script><script>
setTimeout(async()=>{
 document.getElementById('tlNav').click();
 await new Promise(r=>setTimeout(r,0));
 const board=document.getElementById('tlBoard');
 const columnTrigger=document.getElementById('tlColumnsOpen'),columnDialog=document.getElementById('tlColumnDialog');
 const triggerBefore=columnTrigger.getBoundingClientRect();
 const contrast=(foreground,background)=>{const channels=value=>value.match(/[0-9.]+/g).slice(0,3).map(Number);const linear=x=>{x/=255;return x<=.04045?x/12.92:Math.pow((x+.055)/1.055,2.4);};const luminance=value=>{const [r,g,b]=channels(value);return .2126*linear(r)+.7152*linear(g)+.0722*linear(b);};const a=luminance(foreground),b=luminance(background);return(Math.max(a,b)+.05)/(Math.min(a,b)+.05);};
 columnTrigger.focus();columnTrigger.click();await new Promise(r=>setTimeout(r,0));
 const firstColumnToggle=columnDialog.querySelector('input[type="checkbox"]');
 const popupOpen=columnDialog.open&&columnTrigger.getAttribute('aria-expanded')==='true';
 const popupFocus=document.activeElement===firstColumnToggle;
 const popupRows=columnDialog.querySelectorAll('input[data-linear-column-toggle]').length;
 const popupBox=columnDialog.getBoundingClientRect();
 const triggerAfter=columnTrigger.getBoundingClientRect();
 const popupNoShift=Math.abs(triggerBefore.x-triggerAfter.x)<.5&&Math.abs(triggerBefore.y-triggerAfter.y)<.5&&Math.abs(triggerBefore.width-triggerAfter.width)<.5;
 const lightContrast=contrast(getComputedStyle(columnDialog).color,getComputedStyle(columnDialog).backgroundColor);
 document.documentElement.dataset.appTheme='dark';
 const darkContrast=contrast(getComputedStyle(columnDialog).color,getComputedStyle(columnDialog).backgroundColor);
 const popupFits=popupBox.left>=0&&popupBox.right<=innerWidth&&popupBox.top>=0&&popupBox.bottom<=innerHeight;
 const escape=new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true});columnDialog.dispatchEvent(escape);
 const immediateKeyboardClose=!columnDialog.open&&columnTrigger.getAttribute('aria-expanded')==='false'&&document.activeElement===columnTrigger;
 await new Promise(r=>setTimeout(r,0));
 const keyboardClose=immediateKeyboardClose&&!columnDialog.open&&columnTrigger.getAttribute('aria-expanded')==='false'&&document.activeElement===columnTrigger;
 document.documentElement.dataset.appTheme='light';
 const measure=el=>({width:el.clientWidth,scroll:el.scrollWidth,overflow:getComputedStyle(el).overflowX});
 const top=[...board.children].map(x=>x.getBoundingClientRect().top);
 board.scrollLeft=board.scrollWidth;
 const moved=board.scrollLeft>0;
 document.querySelector('main').insertAdjacentHTML('beforeend','<div class="table" id="dealsList"><div class="row"><div>Long readable title '+ 'words '.repeat(50)+'</div><div class="hide-mobile">Stage</div><div class="hide-mobile">Message</div><div>Date</div></div></div><div class="table" id="nativeCompaniesList"><div class="row"><div>Company</div><div><button>Edit</button></div></div></div><section id="supportCases"><div class="support-cases-list"><table class="support-cases-table"><thead><tr>'+Array.from({length:8},(_,i)=>'<th>Column '+i+'</th>').join('')+'</tr></thead><tbody><tr>'+Array.from({length:8},(_,i)=>'<td>Value '+i+'</td>').join('')+'</tr></tbody></table></div></section>');
 const list=document.getElementById('dealsList'),cases=document.querySelector('.support-cases-list');
 list.scrollLeft=100;cases.scrollLeft=100;
 await new Promise(r=>setTimeout(r,200));
 const range=document.querySelector('#appHorizontalScroll input');
 range.value='500';range.dispatchEvent(new Event('input',{bubbles:true}));
 const controlRect=document.getElementById('appHorizontalScroll').getBoundingClientRect();
 const twoCellList=document.getElementById('nativeCompaniesList');
 const result={twoCellList:measure(twoCellList),controlHidden:document.getElementById('appHorizontalScroll').hidden,controlWidth:controlRect.width,controlRight:controlRect.right,rangeValue:range.value,viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,board:measure(board),sameRow:top.every(x=>x===top[0]),moved,cardWrap:getComputedStyle(document.querySelector('.tl-card')).overflowWrap,list:measure(list),listMoved:list.scrollLeft>0,columnsVisible:[...list.querySelectorAll('.hide-mobile')].every(x=>getComputedStyle(x).display!=='none'),cases:measure(cases),casesMoved:cases.scrollLeft>0,caseRow:getComputedStyle(cases.querySelector('tbody tr')).display,caseColumns:[...cases.querySelectorAll('td')].every(x=>getComputedStyle(x).display==='table-cell'),columnPopup:popupOpen&&popupRows===6,columnPopupFocus:popupFocus,columnPopupNoShift:popupNoShift,columnPopupFits:popupFits,columnLightContrast:lightContrast,columnDarkContrast:darkContrast,columnKeyboardClose:keyboardClose,riskPanel:!!document.getElementById('tlRiskReview'),riskFetch:requests.some(x=>x.includes('/risks/'))};
 document.body.insertAdjacentHTML('beforeend','<pre id="result">'+JSON.stringify(result)+'</pre>');
},0);
</script>`;
if (process.argv[2] === '--fixture') { fs.writeFileSync(process.argv[3],fixture); fs.rmSync(dir,{recursive:true,force:true}); process.exit(0); }
try {
 fs.writeFileSync(file,fixture);
 for (const width of [1440,800,500,390]) {
  const run=spawnSync(process.env.CHROMIUM||'chromium',[`--user-data-dir=${dir}/profile-${width}`,'--headless','--no-sandbox','--disable-gpu','--no-proxy-server','--allow-file-access-from-files',`--window-size=${width},1000`,'--virtual-time-budget=3000','--dump-dom',`file://${file}`],{encoding:'utf8',maxBuffer:8*1024*1024,timeout:30000});
  assert.equal(run.status,0,run.stderr);
  const match=run.stdout.match(/<pre id="result">(\{[^<]+\})<\/pre>/);
  assert(match,'browser completed fixture: '+run.stdout.slice(-5000));
  const r=JSON.parse(match[1]);
  assert(r.sameRow&&r.moved&&r.board.scroll>r.board.width,'Linear columns scroll in one row');
  assert.equal(r.cardWrap,'anywhere');
  assert(r.columnPopup,'status selector opens a dialog with one control per status');
  assert(r.columnPopupFocus,'the first checkbox receives keyboard focus on open');
  assert(r.columnKeyboardClose,'Escape closes the dialog and restores focus to its trigger');
  assert(r.columnPopupNoShift,'opening the status dialog does not move toolbar controls');
  assert(r.columnPopupFits,'dialog remains inside desktop/mobile viewport');
  assert(r.columnLightContrast>=4.5&&r.columnDarkContrast>=4.5,'status labels meet contrast in light and dark themes');
  assert(r.documentWidth<=r.viewport,'no page-level horizontal overflow');
  assert(r.columnsVisible&&r.caseColumns,'table columns remain accessible');
  assert.equal(r.caseRow,'table-row');
  assert(!r.riskPanel&&!r.riskFetch,'risk review absent from Linear');
  assert(!r.controlHidden&&r.controlWidth<=r.viewport*.3&&r.controlRight<=r.viewport,'compact bottom-right control');
  if(width<=760) assert(r.twoCellList.scroll<=r.twoCellList.width,'ordinary two-cell lists remain responsive');
  if(r.list.width<720) assert(r.listMoved,'narrow list scrolls');
  if(r.cases.width<980) assert(r.casesMoved,'narrow case table scrolls');
  console.log('PASS:',JSON.stringify(r));
 }
} finally {fs.rmSync(dir,{recursive:true,force:true});}
