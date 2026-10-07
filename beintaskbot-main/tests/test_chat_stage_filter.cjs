const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../docs/index.html'),'utf8');
const functions=['chatStageFilterKey','chatStageFilterLabel','renderChatsStageFilter'].map(name=>source.match(new RegExp(`function ${name}\\([^]*?\\n\\}`))[0]).join('\n');
const constants=source.match(/const NIZAMI_PIPELINE_ID = [^]*?const NIZAMI_CHAT_FILTER_STAGES = \[[^]*?\];/)[0];
const box={innerHTML:'',querySelectorAll:()=>[],querySelector:()=>({})};
const rows=[{id:1,pipeline_id:14243944,status_id:112086096,stage_name:'təlimat'},
 {id:2,pipeline_id:14243944,status_id:142,stage_name:'Успешно реализовано'},
 {id:3,pipeline_id:999,stage_key:'other',stage_name:'Other employee stage'},
 {id:4,pipeline_id:14243944,stage_key:'nerazobrannoye',stage_name:'Неразобранное'}];
const context={isAdmin:true,document:{getElementById:()=>box},_chatsStageFilters:new Set(['999:other','14243944:telimat']),
 chatInboxSourceDeals:()=>rows,chatBelongsToViewer:()=>true,isTerminalChatDeal:row=>row.status_id===142,
 escapeHtml:String,renderChatsList:()=>{},_chatsVisibleLimit:60};
vm.createContext(context);vm.runInContext(constants+'\n'+functions,context);
vm.runInContext('renderChatsStageFilter()',context);
assert.equal((box.innerHTML.match(/type="checkbox"/g)||[]).length,6);
for(const label of ['yeni sorgu','danışıqlar','təlimat','yeni sifariş','gözləmə','Müzakirə'])assert(box.innerHTML.includes('Nizami Qasımov · '+label));
assert(!box.innerHTML.includes('Other employee'));assert(!box.innerHTML.includes('Неразобранное'));assert(!box.innerHTML.includes('Успешно'));
assert(!context._chatsStageFilters.has('999:other'));assert(context._chatsStageFilters.has('14243944:telimat'));
assert(box.innerHTML.indexOf('yeni sorgu')<box.innerHTML.indexOf('danışıqlar'));
vm.runInContext('key=chatStageFilterKey({pipeline_id:14243944,status_id:112086096,stage_name:"təlimat"})',context);
assert.equal(context.key,'14243944:telimat');
context.isAdmin=false;vm.runInContext('renderChatsStageFilter()',context);
assert(box.innerHTML.includes('Other employee stage'),'Employee filters retain their own inbox stages');
rows.length=0;context.isAdmin=true;vm.runInContext('renderChatsStageFilter()',context);
assert.equal((box.innerHTML.match(/type="checkbox"/g)||[]).length,6,'Empty stages remain selectable');
for(const match of source.matchAll(/<script(?:\s[^>]*)?>([^]*?)<\/script>/g))if(match[1].trim())new vm.Script(match[1]);
console.log('PASS: administrator has exactly six ordered Nizami stages, numeric statuses match selections, other employee filters remain unchanged.');
