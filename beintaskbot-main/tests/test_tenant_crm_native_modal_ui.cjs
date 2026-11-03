const fs = require('node:fs');
const assert = require('node:assert/strict');
const app = fs.readFileSync(`${__dirname}/../docs/tenant-app.html`, 'utf8');
const sync = fs.readFileSync(`${__dirname}/../docs/tenant-crm-sync.js`, 'utf8');
const contacts = fs.readFileSync(`${__dirname}/../docs/tenant-contacts-directory.js`, 'utf8');
function view(id) {
  const start = app.indexOf(`id="view-${id}"`);
  assert(start >= 0, `missing ${id} view`);
  const end = app.indexOf('</section>', start);
  return app.slice(start, end);
}
for (const [viewId, createId, formId, modalId] of [
  ['tasks', 'nativeTaskCreate', 'nativeTaskForm', 'nativeTaskModal'],
  ['deals', 'nativeDealCreate', 'nativeDealForm', 'nativeDealModal'],
  ['customers', 'nativeContactCreate', 'nativeContactForm', 'nativeContactModal'],
  ['companies', 'nativeCompanyCreate', 'nativeCompanyForm', 'nativeCompanyModal'],
]) {
  assert(view(viewId).includes(`id="${createId}"`), `${viewId}: create action missing`);
  assert(!view(viewId).includes(`id="${formId}"`), `${viewId}: inline form remains`);
  const modalStart = app.indexOf(`id="${modalId}"`);
  const formAt = app.indexOf(`id="${formId}"`);
  const modalEnd = app.indexOf('</section></div>', modalStart);
  assert(modalStart >= 0 && formAt > modalStart && formAt < modalEnd, `${formId} must be inside modal`);
  assert(app.includes(`data-crm-close="${modalId}"`), `${modalId}: cancel/close missing`);
  const modal=app.slice(modalStart,modalEnd);
  assert.equal((modal.match(/type="submit"/g)||[]).length,1,`${modalId}: exactly one save action`);
  assert(modal.includes('crm-modal-actions') && modal.includes('Ləğv et') && modal.includes('Yadda saxla'),`${modalId}: cancel and save share the action row`);
}
assert(app.includes('data-view="companies"') && app.includes('data-crm-nav-label="companies"'), 'companies live inside the CRM navigation group');
assert(app.includes("ru:{group:'CRM'") && app.includes("en:{group:'CRM'") && app.includes('tenant-language'), 'CRM navigation supports RU/AZ/EN');
// Contact handlers moved into the real directory asset; the contact/deal/task
// workflow suite exercises the actual buttons rather than opening fake modals.
assert(app.includes('/assets/tenant-contacts-directory.js') && contacts.includes("nativeContactCreate').onclick=()=>void openForm('create')") && app.includes("nativeDealCreate').onclick=async") && app.includes("nativeTaskCreate').onclick=async") && app.includes("nativeCompanyCreate').onclick=async"), 'all four create buttons bind their working forms in the current modules');
assert(contacts.includes("nativeContactForm').onsubmit=saveForm") && contacts.includes('async function saveForm(event)') && app.includes("nativeDealForm').onsubmit=async") && app.includes("nativeTaskForm').onsubmit=async") && app.includes("nativeCompanyForm').onsubmit=async"), 'all entity forms persist through their current API controllers');
assert(!app.includes('id="nativeTaskReset"'), 'task modal has only one save/create action');
assert(!app.includes('id="nativeDealReset"')&&!app.includes('id="nativeCompanyReset"'),'deal/company cards have no duplicate create/reset action');
assert(!app.includes('instruction-library-nav')&&!app.includes('data-instruction-label'),'Instructions are no longer a sidebar item');
assert(app.includes("Saxlanılmamış dəyişikliklər var"), 'closing an edited form asks before discarding changes');
for (const id of ['customerSearch','customerCount','customersList','contactsSource']) assert.equal((view('customers').match(new RegExp(`id="${id}"`,'g'))||[]).length,1,`one contact directory ${id}`);
assert(!view('customers').includes('nativeContactsList')&&!view('customers').includes('nativeContactSearch'),'the selected source uses one visible contact list and query');
assert(view('companies').includes('companySearch') && view('companies').includes('nativeCompaniesList'));
assert(!view('companies').includes('nativeCompanyLinkContact') && !view('companies').includes('nativeCompanyLinkDeal'), 'company associations belong inside the company card');
assert(app.includes('nativeEntityCard') && app.includes('data-company-tab=\"contacts\"') && app.includes('data-company-tab=\"deals\"'), 'company card exposes relationship tabs');
assert(contacts.includes("['general','companies','deals','dialogs']") && contacts.includes('data-contact-panel="companies"') && contacts.includes('data-contact-panel="deals"') && contacts.includes('data-contact-panel="dialogs"') && contacts.includes("t('dialogsHelp')"), 'the current contact card retains company/deal relations and explains its dialog limitation');
assert(app.includes('data-deal-tab=\"contacts\"') && app.includes('data-deal-tab=\"company\"') && app.includes('data-deal-tab=\"tasks\"'), 'deal card exposes contact/company/task relations');
assert(app.includes('data-task-tab=\"related\"') && app.includes('başqa sövdələşməyə keçirmək hazırkı API-də mövcud deyil'), 'task card shows its real links and reports that reassignment is unsupported');
assert(app.includes('.top{align-items:center;flex-wrap:wrap}') && app.includes('.crm-toolbar-actions{width:100%;justify-content:flex-start;flex-wrap:wrap}'), 'CRM action rows wrap at mobile widths without horizontal overflow');
assert(app.includes("Arxivlənsin?\\n\\nQeyd: ${title}"), 'archive confirmation identifies the record title');
assert(contacts.includes("action:'link_contact'") && contacts.includes("action:'unlink_contact'") && app.includes("action:'link_deal'") && app.includes("action:'unlink_deal'"), 'contact and deal relations retain their existing tenant-scoped company APIs in the current controllers');
for (const type of ['company', 'deal', 'task']) assert(app.includes(`data-native-open=\"${type}:`), `${type}: entity card action missing`);
assert(app.includes(`data-native-open="'+(kind==='contact'?'contact':'deal')+':`), 'company relation rows retain exact contact/deal card targets');
assert(view('integrations').includes('externalCrmSyncPanel'), 'sync settings belong in existing integrations view');
assert(!view('deals').includes('externalCrmSyncPanel'));
assert(!app.includes('Beintaskbot UUID:') && !app.includes('nativeContactKommoId'));
assert(app.includes('button.disabled)return') && app.includes('showCrmModalError'), 'submits must prevent double-save and retain/report errors');
assert(app.includes("confirm('Ayırmaq istəyirsiniz? Qeyd özü silinməyəcək.')"));
assert(sync.includes("beinsystems:['title','pipeline_id','stage_id','contact_id','description']"));
assert(sync.includes("['off','to_external',...(p==='kommo'?['from_external','bidirectional']:[])]"));
assert(sync.includes("contact:['to_external','from_external','bidirectional']") === false); // capabilities are rendered from the API response
assert(sync.includes('Dəstəklənmir') || sync.includes('dəstəklənmir'));
assert(sync.includes('Fərdi xəta detalları API-dən əlçatan deyil.') || sync.includes('fərdi xəta detalları API-dən əlçatan deyil.'));
console.log('PASS: CRM sections use separate modals, contacts/companies navigation is native, and sync UI reflects API capabilities.');
