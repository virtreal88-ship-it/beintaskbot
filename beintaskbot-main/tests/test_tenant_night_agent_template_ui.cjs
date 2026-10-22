// Regression contract for the Night technical support AI Agent template.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'docs', 'tenant-night-agent-template.js'), 'utf8');
const app = fs.readFileSync(path.join(root, 'docs', 'tenant-app.html'), 'utf8');
const assets = fs.readFileSync(path.join(root, 'tenant_web_assets.py'), 'utf8');
const start = source.indexOf('function nightAgentSchedule(');
const end = source.indexOf('\n async function loadProfile', start);
assert(start >= 0 && end > start, 'The tested template helpers must remain DOM-independent.');
const helpers = vm.runInNewContext(`const DEFAULT_MODEL='__platform_default__';${source.slice(start, end)};({nightAgentSchedule,nightAgentScope,nightAgentPayload})`);

const overnight = helpers.nightAgentSchedule('Asia/Baku', [0, 1, 2, 3, 4], '22:00', '06:00');
assert.deepEqual(JSON.parse(JSON.stringify(overnight)), {
  timezone: 'Asia/Baku', days: [0, 1, 2, 3, 4], start: '22:00', end: '06:00',
}, 'The wizard must preserve an overnight window instead of turning it into a daytime schedule.');

const base = {
  name: 'Ночная техническая поддержка',
  instructions: 'Отвечай только по базе знаний.',
  timezone: 'Asia/Baku', days: [0, 1, 2, 3, 4], start: '22:00', end: '06:00',
  channelId: 'connect-channel-a', model: '__platform_default__', mode: 'manual', confirmedLaunch: false,
};
const disabled = helpers.nightAgentPayload(base);
assert.equal(disabled.enabled, false, 'A template remains disabled until the operator explicitly confirms launch.');
assert.equal(disabled.mode, 'manual', 'Manual is the safe default.');
assert.deepEqual(JSON.parse(JSON.stringify(disabled.scopes)), [{
  provider: 'connect', pipeline_id: '', stage_id: '', channel_id: 'connect-channel-a', instructions: '',
}], 'The selected Connect channel must be the only saved scope; no Kommo scope is introduced.');
assert.equal(disabled.operating_mode, undefined, 'The template must not change company Active Mode.');
assert.equal(disabled.auto_send_enabled, undefined, 'The template must not enable company auto-send.');

const confirmedAutomatic = helpers.nightAgentPayload({...base, mode: 'automatic', confirmedLaunch: true, model: 'gpt-5.6'});
assert.equal(confirmedAutomatic.mode, 'automatic');
assert.equal(confirmedAutomatic.enabled, true, 'Automatic can only be enabled after an explicit confirmation.');
const internal = helpers.nightAgentPayload({...base, role: 'expert', scopes: [], mode: 'automatic'});
assert.equal(internal.role, 'expert', 'The internal wizard path must use the existing server-supported expert role.');
assert.equal(internal.mode, 'manual', 'An internal agent must not acquire external automatic sending.');
assert.deepEqual(JSON.parse(JSON.stringify(internal.scopes)), [], 'An internal agent must not acquire a WhatsApp scope.');
assert.deepEqual(JSON.parse(JSON.stringify(internal.expert_policy)), {internal: true, allowed_primary_ids: []}, 'Internal permissions remain explicit and tenant-scoped.');
const retryPayload = helpers.nightAgentPayload({...base, setupKey: 'night-wizard-retry-0001'});
assert.equal(retryPayload.setup_key, 'night-wizard-retry-0001', 'A resumed wizard must send its opaque retry key.');
assert.throws(() => helpers.nightAgentScope(''), /WhatsApp/, 'A missing Connect channel must fail before saving.');
assert.throws(() => helpers.nightAgentSchedule('Asia/Baku', [0], '22:00', '22:00'), /разные часы/, 'An ambiguous 24-hour shift is rejected.');

assert.match(source, /expected_tenant_id:current\.tenant_id/,
  'Every JSON mutation must carry the selected tenant identity for the existing server check.');
assert.match(source, /expected_user_id:current\.telegram_id/,
  'Every JSON mutation must carry the current employee identity for the existing server check.');
assert.match(source, /provider:'connect'/, 'The template must work through Connect without Kommo.');
assert.match(source, /actions:\['handoff'\]/, 'Complex/unsolved requests must use the existing handoff action.');
assert.match(source, /knowledge\/upload/, 'Knowledge files must use the existing upload endpoint.');
assert.match(source, /localStorage\.setItem/, 'An interrupted setup must be recoverable after a reload.');
assert.match(source, /data-night-entry="0"/, 'The existing wizard must start by selecting the agent type.');
assert.match(source, /data-night-entry="1"/, 'The existing wizard must then select a template or a blank start.');
assert.match(source, /agentRole==='expert'\?\[\]/, 'Internal agents must skip external channel scopes.');
assert.match(source, /confirm\(window\.tenantNightL10n\.text\('Закрыть мастер/, 'Closing a dirty wizard must require confirmation.');
assert.match(source, /setup_key/, 'Agent, handoff and knowledge retries must use idempotency keys.');
assert.match(source, /Настройки автоматической отправки компании мастер не меняет|Мастер не меняет разрешение автоматической отправки компании/i,
  'The UI must explain that agent setup does not change the company auto-send permission.');
assert.doesNotMatch(source, /api\('agents\/settings','PATCH'/,
  'The template must not patch company settings while being created.');
for (const phrase of ['вежливый', 'только на основе доступной базы знаний', 'уточняющий вопрос', 'Не выдумывайте', 'нерешённое обращение', 'выбранные дни и часы']) {
  assert.match(source, new RegExp(phrase, 'i'), `Template instruction must include: ${phrase}`);
}
assert.match(app, /id="aiNightTemplateOpen"/, 'The existing AI Agents view must expose the template.');
assert.match(app, /tenant-night-agent-template\.js/, 'The template script must be loaded by the tenant app.');
assert.match(assets, /tenant-night-agent-template\.js/, 'The template asset must be served through the fixed asset catalogue.');

console.log('PASS: night support template preserves Connect isolation, safe launch defaults, overnight schedules, and existing tenant-scoped APIs.');
