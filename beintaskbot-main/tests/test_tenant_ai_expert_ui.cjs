const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
for(const lang of ['ru','az','en']){
const dom=new JSDOM(`<div id="aiAgentList"></div><div id="aiAgentEditor"><section data-ai-editor-section="basics"></section><select id="aiAgentMode"><option value="manual">Manual</option><option value="automatic">Automatic</option></select></div>`,{runScripts:'outside-only',url:'https://test.local/app?site_lang='+lang});
const w=dom.window,$=id=>w.document.getElementById(id);w.eval(fs.readFileSync('docs/tenant-ai-expert-editor.js','utf8'));
const agents=[{id:'e1',role:'expert',enabled:true,name:'<img src=x>',model:'m1'},{id:'e2',role:'expert',enabled:true,name:'Training',model:'m2'},{id:'p',role:'primary',enabled:true,name:'Primary'}];
const primary={id:'p',role:'primary',expert_policy:{expert_agent_ids:['e1','e2'],max_calls:3,tools:['system_status']}};
w.aiExpertEditor.render(primary,agents,()=>{});
assert.equal($('aiExpertAgent').selectedOptions.length,2);
assert.deepEqual(Array.from(w.aiExpertEditor.read().expert_policy.expert_agent_ids),['e1','e2']);
assert.equal(w.aiExpertEditor.read().expert_policy.max_calls,3);assert.match($('aiExpertModel').textContent,/m1.*m2/);
w.aiExpertEditor.teams([primary,...agents.filter(a=>a.id!=='p')]);assert.equal(w.document.querySelector('img'),null);assert.match($('aiTeams').textContent,/Training/);assert.doesNotMatch($('aiTeams').textContent,/e1|e2/);
$('aiAgentRole').value='expert';$('aiAgentRole').dispatchEvent(new w.Event('change'));
assert.equal($('aiAgentMode').value,'manual');assert.equal($('aiAgentMode').disabled,true);assert.equal($('aiExpertBinding').hidden,true);
assert.equal(w.aiExpertEditor.read().expert_policy.internal,true);
w.aiExpertEditor.render({role:'expert'},agents,()=>{});$('aiAllowedPrimaries').options[0].selected=true;assert.deepEqual(Array.from(w.aiExpertEditor.read().expert_policy.allowed_primary_ids),['p']);
w.aiExpertEditor.render({id:'e1',role:'expert',expert_policy:{}},agents,()=>{});
assert.deepEqual(JSON.parse(JSON.stringify(w.aiExpertEditor.read())),{role:'expert',expert_policy:{}});
w.aiExpertEditor.render({id:'p',expert_policy:{expert_agent_id:'e1'}},agents,()=>{});
assert.equal(w.aiExpertEditor.read().expert_policy.expert_agent_id,'e1');
assert.equal($('aiAgentMode').disabled,false);dom.window.close();
}
console.log('PASS AI teams: RU/AZ/EN, helpers, individual permissions, legacy preservation, escaping and no visible IDs.');
