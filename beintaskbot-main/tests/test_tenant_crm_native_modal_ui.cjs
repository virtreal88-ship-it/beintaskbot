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
assert(app.includes('data-view="companies"') && app.includes('Şirkətlər'));
assert(view('customers').includes('nativeContactsList'), 'Kommo client list and native contacts must coexist');
assert(view('companies').includes('companySearch') && view('companies').includes('nativeCompaniesList'));
assert(!view('companies').includes('nativeCompanyLinkContact') && !view('companies').includes('nativeCompanyLinkDeal'), 'company associations belong inside the company card');
assert(app.includes('nativeEntityCard') && app.includes('data-company-tab=\"contacts\"') && app.includes('data-company-tab=\"deals\"'), 'company card exposes relationship tabs');
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
