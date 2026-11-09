// Test real frontend functions with DOM/API stubs; no live task creation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'docs', 'index.html'), 'utf8');
function source(name) {
    const marker = new RegExp(`^(?:async )?function ${name}\\(`, 'm');
    const match = marker.exec(html);
    assert(match, `Function not found: ${name}`);
    const rest = html.slice(match.index + match[0].length);
    const next = /^(?:async )?function \w+\(/m.exec(rest);
    return html.slice(match.index, next ? match.index + match[0].length + next.index : html.length);
}

async function test() {
    const button = {hidden: true, style: {display: 'none'}};
    const operatorFilter = {value: 'admin@nizam', hidden: false, style: {}};
    const select = initial => {
        const value = {value: initial, options: [], _html: ''};
        Object.defineProperty(value, 'innerHTML', {get() {return this._html;}, set(html) {
            this._html = html;
            this.options = [...html.matchAll(/<option value="([^"]+)"([^>]*)>(.*?)<\/option>/g)]
                .map(match => ({value: match[1], selected: /\bselected\b/.test(match[2]), text: match[3]}));
        }});
        Object.defineProperty(value, 'selectedOptions', {get() {return this.options.filter(option => option.selected);}});
        return value;
    };
    const statusFilter = select('all');
    const columnFilter = select('');
    const savedSettings = new Map();
    const board = {innerHTML: '', querySelectorAll: () => [], querySelector: () => null};
    let modal;
    const context = {
        console, setTimeout, clearTimeout, AbortController, API_BASE: '', CURRENT_USER_ID: '6824377548', TELEGRAM_INIT_DATA: '',
        isAdmin: false, employeePermissions: new Set(), employeeLinearCanCreate: false,
        canReviewNews: () => false,
        HAS_CHATS: false, taskTypeTab: 'tasks', localStorage: {getItem(key) {return savedSettings.get(key) || null;}, setItem(key, value) {savedSettings.set(key, value);}},
        document: {
            getElementById(id) {
                if(id === 'linear-page-create') return button;
                if(id === 'linear-operator-filter') return operatorFilter;
                if(id === 'linear-status-filter') return statusFilter;
                if(id === 'linear-column-filter') return columnFilter;
                if(id === 'linear-tasks-container') return {querySelector: () => board};
                return null;
            },
            createElement() {return {addEventListener() {}};},
            body: {appendChild(element) {modal = element;}},
        },
        window: {setTimeout() {}, matchMedia: () => ({matches: false})},
        CSS: {escape: value => value},
        async fetch() {
            return {ok: true, async json() {return {success: true, active: true, chat_id: '6824377548', permissions: ['linear'], linear_can_create: true};}};
        },
        hasEmployeePermission(permission) {return context.employeePermissions.has(permission);},
        setTaskTypeTab() {}, setPendingTabVisible() {}, closeLinearModal() {}, toast() {},
        escapeHtml: value => String(value || ''),
        bindLinearColumnDrop() {}, bindLinearTaskCard() {},
        linearBoardCardHtml: row => `<article>${row.id}</article>`,
        linearBoardStatuses: [{id: 'testiq', name: 'Testiq', position: 10}, {id: 'triage', name: 'Triage', position: 20}], linearBoardProjects: [],
        linearBoardRows: [
            {id: 'own-1', source_id: 'own-1', client: 'rufet', operator: 'nurane@beinsystems', status: {id: 'triage'}},
            {id: 'own-2', source_id: 'own-2', client: 'rufet', operator: 'sermaye@beinsystems', status: {id: 'testiq'}},
            {id: 'accountless-task', source_id: 'accountless-task', client: '', operator: 'nurane@beinsystems', status: {id: 'triage'}},
        ],
    };
    vm.createContext(context);
    for(const name of ['loadEmployeeSession', 'applyEmployeePermissionUi', 'openLinearCreateModal', 'linearBoardTaskMatches', 'linearBoardOperatorMatches', 'linearBoardHasAccount', 'renderLinearBoard', 'saveLinearBoardColumns', 'resetLinearBoardColumns']) {
        vm.runInContext(source(name), context);
    }
    await context.loadEmployeeSession();
    assert(!context.linearBoardHasAccount({client: 'Account göstərilməyib'}));
    assert.equal(button.hidden, false);
    assert.equal(button.style.display, '');
    assert(!context.employeePermissions.has('linear_edit'));
    await context.openLinearCreateModal();
    assert(modal.innerHTML.includes('linear-create-title'));
    assert(!modal.innerHTML.includes('linear-create-account'));
    assert(!modal.innerHTML.includes('linear-create-operator'));
    for(const desktop of [false, true]) {
        context.window.matchMedia = () => ({matches: desktop});
        context.renderLinearBoard();
        assert(board.innerHTML.includes('own-1') && board.innerHTML.includes('own-2'));
        assert(!board.innerHTML.includes('accountless-task'));
        assert(board.innerHTML.indexOf('data-linear-status-column="testiq"') < board.innerHTML.indexOf('data-linear-status-column="triage"'));
        assert.equal(operatorFilter.hidden, true);
        assert.equal(operatorFilter.style.display, 'none');
    }
    columnFilter.options.forEach(option => { option.selected = option.value === 'triage'; });
    context.saveLinearBoardColumns();
    assert.equal(savedSettings.get('linear-board-hidden-columns:6824377548'), JSON.stringify(['testiq']));
    assert(board.innerHTML.includes('data-linear-status-column="triage"'));
    assert(!board.innerHTML.includes('data-linear-status-column="testiq"'));
    context.resetLinearBoardColumns();
    assert(board.innerHTML.includes('data-linear-status-column="testiq"'));
    context.employeeLinearCanCreate = false;
    context.applyEmployeePermissionUi();
    assert.equal(button.hidden, true);
    assert.equal(button.style.display, 'none');
    modal = null;
    await context.openLinearCreateModal();
    assert.equal(modal, null);
    console.log('PASS: session grants create-only access; button and form open; desktop/mobile ignore a hidden stale operator filter; closed access stays hidden.');
}

function testStaffCardActions() {
    const context = {isAdmin: false, hasEmployeePermission: () => true,
        escapeHtml: value => String(value || ''), Date};
    vm.createContext(context);
    for(const name of ['isLinearDoneStatus', 'linearBoardPriorityStyle', 'linearBoardStatusStyle', 'linearBoardPriorityLabel', 'formatLinearTimestamp', 'linearBoardCardHtml', 'bindLinearTaskCard', 'bindLinearColumnDrop']) {
        vm.runInContext(source(name), context);
    }
    const task = {source_id: 'id', title: 'Task', client: 'rufet', priority: 2, in_confirmation: true, status: {name: 'Triage'}};
    let card = context.linearBoardCardHtml(task);
    assert(card.includes('draggable="false"'));
    assert(!card.includes('deal-drag-handle'));
    assert(!card.includes('<button') && !card.includes('<select'));
    task.status = {name: 'Done', type: 'completed'};
    card = context.linearBoardCardHtml(task);
    assert(card.includes('Test olundu') && card.includes('Testdən keçmədi'));
    assert(!card.includes('Diskussiya') && !card.includes('Təsdiq edirəm'));
    assert.equal(context.isLinearDoneStatus({name: 'Accept', type: 'completed'}), false);
    const element = {dataset: {}, addEventListener() {throw Error('Staff must not have drag handlers');}};
    context.bindLinearTaskCard(element, task);
    context.bindLinearColumnDrop(element);
    context.isAdmin = true;
    assert(context.linearBoardCardHtml(task).includes('draggable="true"'));
    console.log('PASS: staff cards are read-only except Done testing; legacy edit rights cannot enable drag/drop.');
}

function testAdminConfirmationActions() {
    const context = {isAdmin: true, hasEmployeePermission: () => true,
        escapeHtml: value => String(value || ''), Date};
    vm.createContext(context);
    for(const name of ['linearIssueCardHtml', 'isLinearDoneStatus', 'linearBoardPriorityStyle', 'linearBoardStatusStyle', 'linearBoardPriorityLabel', 'formatLinearTimestamp', 'linearBoardCardHtml']) {
        vm.runInContext(source(name), context);
    }
    const task = {id: 'BS-1', source_id: 'id', url: 'https://linear.app/acme/BS-1', title: 'Task', client: 'rufet', priority: 2, in_confirmation: true, status: {name: 'Triage'}};
    for(const card of [context.linearIssueCardHtml(task), context.linearBoardCardHtml(task)]) {
        assert(card.includes('href="https://linear.app/acme/BS-1"'));
        assert(card.includes("runLinearTesdiqAction('to_testiq'"));
        assert(card.includes('>Təsdiq</button>'));
        const menuStart = card.indexOf('<details');
        const cancelAction = card.indexOf("openLinearCancelModal('id')");
        assert(menuStart >= 0 && cancelAction > menuStart, 'İmtina must be inside the overflow menu');
    }
    console.log('PASS: admin cards show Təsdiq in the main row and keep İmtina inside the overflow menu.');
}

testStaffCardActions();
testAdminConfirmationActions();
test().catch(error => {console.error(error); process.exitCode = 1;});
