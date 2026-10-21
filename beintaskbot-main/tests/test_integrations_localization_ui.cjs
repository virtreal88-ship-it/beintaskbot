const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
const html=fs.readFileSync('docs/tenant-app.html','utf8');
const i18n=fs.readFileSync('docs/tenant-operator-i18n.js','utf8');
const dom=new JSDOM(html,{url:'https://example.test/app?site_lang=az',runScripts:'outside-only'});
const {document}=dom.window;
dom.window.eval(i18n);
const expected={
  ru:['КАТАЛОГ ИНТЕГРАЦИЙ','Интеграции','Управляйте подключениями компании в одном месте.','Синхронизация CRM'],
  az:['İNTEQRASİYA KATALOQU','İnteqrasiyalar','Şirkət bağlantılarını bir yerdən idarə edin.','CRM sinxronizasiyası'],
  en:['INTEGRATION MARKET','Integrations','Manage company connections in one place.','CRM synchronization'],
};
const syncErrors={ru:'Не удалось загрузить настройки синхронизации.',az:'Sinxronizasiya ayarlarını yükləmək mümkün olmadı.',en:'Could not load synchronization settings.'};
for(const [locale,values] of Object.entries(expected)){
  dom.window.tenantI18n.setLanguage(locale);
  assert.deepEqual(['integrationMarket','integrations','integrationsDescription','crmSyncTitle'].map(key=>document.querySelector(`[data-i18n="${key}"]`).textContent),values);
  assert.equal(dom.window.tenantI18n.t('crmSyncLoadError'),syncErrors[locale]);
}
assert(document.querySelector('#externalCrmSyncSave[data-i18n="crmSyncSave"]'));
dom.window.close();
console.log('PASS: integration catalog and CRM sync labels switch RU/AZ/EN without reload');
