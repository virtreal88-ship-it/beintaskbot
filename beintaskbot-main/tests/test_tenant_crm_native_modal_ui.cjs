const fs = require('node:fs');
const assert = require('node:assert/strict');
const app = fs.readFileSync(`${__dirname}/../docs/tenant-app.html`, 'utf8');
const sync = fs.readFileSync(`${__dirname}/../docs/tenant-crm-sync.js`, 'utf8');
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
}
assert(app.includes('data-view="companies"') && app.includes('data-crm-nav-label="companies"'), 'companies live inside the CRM navigation group');
assert(app.includes("ru:{group:'CRM'") && app.includes("en:{group:'CRM'") && app.includes('tenant-language'), 'CRM navigation supports RU/AZ/EN');
assert(app.includes("nativeContactCreate').onclick=async") && app.includes("nativeDealCreate').onclick=async") && app.includes("nativeTaskCreate').onclick=async") && app.includes("nativeCompanyCreate').onclick=async"), 'all four create buttons open working forms');
assert(app.includes("nativeContactForm').onsubmit=async") && app.includes("nativeDealForm').onsubmit=async") && app.includes("nativeTaskForm').onsubmit=async") && app.includes("nativeCompanyForm').onsubmit=async"), 'all entity forms persist through existing APIs');
assert(!app.includes('id="nativeTaskReset"'), 'task modal has only one save/create action');
assert(app.includes("Saxlanılmamış dəyişikliklər var"), 'closing an edited form asks before discarding changes');
assert(view('customers').includes('nativeContactsList'), 'Kommo client list and native contacts must coexist');
assert(view('companies').includes('companySearch') && view('companies').includes('nativeCompaniesList'));
assert(!view('companies').includes('nativeCompanyLinkContact') && !view('companies').includes('nativeCompanyLinkDeal'), 'company associations belong inside the company card');
assert(app.includes('nativeEntityCard') && app.includes('data-company-tab=\"contacts\"') && app.includes('data-company-tab=\"deals\"'), 'company card exposes relationship tabs');
assert(app.includes('data-contact-tab=\"companies\"') && app.includes('data-contact-tab=\"deals\"') && app.includes('data-contact-tab=\"dialogs\"'), 'contact card shows supported company/deal relations and explains dialog limitation');
assert(app.includes('data-deal-tab=\"contacts\"') && app.includes('data-deal-tab=\"company\"') && app.includes('data-deal-tab=\"tasks\"'), 'deal card exposes contact/company/task relations');
assert(app.includes('data-task-tab=\"related\"') && app.includes('başqa sövdələşməyə keçirmək hazırkı API-də mövcud deyil'), 'task card shows its real links and reports that reassignment is unsupported');
assert(app.includes('.top{align-items:center;flex-wrap:wrap}') && app.includes('.crm-toolbar-actions{width:100%;justify-content:flex-start;flex-wrap:wrap}'), 'CRM action rows wrap at mobile widths without horizontal overflow');
assert(app.includes("Arxivlənsin?\\n\\nQeyd: ${title}"), 'archive confirmation identifies the record title');
assert(app.includes("action:'link_contact'") && app.includes("action:'unlink_contact'") && app.includes("action:'link_deal'") && app.includes("action:'unlink_deal'"), 'relations use existing tenant-scoped company APIs');
for (const type of ['company', 'contact', 'deal', 'task']) assert(app.includes(`data-native-open=\"${type}:`), `${type}: entity card action missing`);
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
