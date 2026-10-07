const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../docs/tenant-app.html'),'utf8');
let count=0;
for(const match of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) {new vm.Script(match[1]);count++;}
assert(count>0);assert(html.includes('data-task-complete='));assert(html.includes('/assets/tenant-task-completion.js'));
console.log('PASS: tenant app inline scripts compile and completion module is linked.');
