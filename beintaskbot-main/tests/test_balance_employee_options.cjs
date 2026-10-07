const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../docs/index.html'), 'utf8');
const start = source.indexOf('async function loadBalanceEmployeeOptions()');
const end = source.indexOf('function escapeBalanceText', start);
const escapeEnd = source.indexOf('\n}', end) + 2;
const context = {API_BASE: '', CURRENT_USER_ID: 1};
vm.createContext(context);
vm.runInContext(source.slice(start, escapeEnd), context);
(async () => {
    context.fetch = async () => ({ok: true, json: async () => ({success: true, employees: [{tg_id: 99, name: 'Rüfət <&>'}]})});
    const options = await context.loadBalanceEmployeeOptions();
    assert(options.includes('value="99"'));
    assert(options.includes('Rüfət &lt;&amp;&gt;'));
    assert(!options.includes('6824377548'));
    assert(options.includes('value=""'));
    for (const response of [{ok: false, success: false}, {ok: true, success: true, employees: []}]) {
        context.fetch = async () => ({ok: response.ok, json: async () => response});
        await assert.rejects(context.loadBalanceEmployeeOptions());
    }
    assert(!source.includes('BALANCE_EMPLOYEE_OPTIONS'));
    console.log('Finance employee options: passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
