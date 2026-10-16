// Regression test for selecting, saving, and reloading Connect agent scopes.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'docs/tenant-ai-agents.js'), 'utf8');
const appSource = fs.readFileSync(path.join(root, 'docs/tenant-app.html'), 'utf8');
const start = source.indexOf('function renderScopeOptions(agent)');
const end = source.indexOf('function editAgent(agent', start);
assert(start >= 0 && end > start, 'AI Agent scope editor must exist');
const renderer = source.slice(start, end).trim();
const serializerStart = source.indexOf('function serializeAgentScopes(');
const serializerEnd = source.indexOf('\n', serializerStart);
assert(serializerStart >= 0 && serializerEnd > serializerStart, 'Agent scope serializer must exist');
const serializer = source.slice(serializerStart, serializerEnd);

function editor(savedAgent) {
  const elements = new Map();
  const ids = ['aiAgentProvider', 'aiKommoScope', 'aiKommoStages', 'aiConnectScopeIntro', 'aiGroupMessagesOption', 'aiAllowGroupMessages', 'aiPipelineId',
    'aiStageId', 'aiChannelId', 'aiPipelineSelected', 'aiStageSelected',
    'aiChannelSelected', 'aiAgentModel'];
  for (const id of ids) elements.set(id, { id, value: '', options: [], dataset: {}, classList: { toggle() {} } });
  const context = {
    agentOptions: { pipelines: [], stages: [], channels: [],
      connect_channels: [{ id: 'wa-channel-42', name: 'WhatsApp 42' }], models: ['test-model'] },
    document: { getElementById: id => elements.get(id) },
    $: id => elements.get(id),
    selectValues: id => elements.get(id).options.filter(option => option.selected).map(option => option.value),
    setOptions: (id, rows, selected) => {
      const element = elements.get(id), values = new Set((selected || []).map(String));
      element.options = rows.map(row => ({ value: String(row.value), label: row.label, selected: values.has(String(row.value)) }));
    },
    renderSelected() {},
  };
  for (const id of ['aiPipelineId', 'aiStageId', 'aiChannelId']) {
    Object.defineProperty(elements.get(id), 'selectedOptions', { get() { return this.options.filter(option => option.selected); } });
  }
  const run = vm.runInNewContext(`${renderer}; renderScopeOptions`, context);
  const serialize = vm.runInNewContext(`${serializer}; serializeAgentScopes`, context);
  run(savedAgent);
  return { elements, context, serialize };
}

// Start from the existing Kommo scope, switch provider and select a Connect channel.
const first = editor({ scopes: [{ provider: 'kommo', pipeline_id: '1', stage_id: '2', channel_id: 'kommo-wa' }] });
const provider = first.elements.get('aiAgentProvider');
provider.value = 'connect';
provider.onchange();
const channel = first.elements.get('aiChannelId');
channel.options[0].selected = true;
channel.onchange();
const submittedScopes = JSON.parse(JSON.stringify(first.serialize(provider.value, first.context.selectValues('aiChannelId'), [],
  [{ provider: 'kommo', pipeline_id: '1', stage_id: '2', channel_id: 'kommo-wa' }])));
assert.deepEqual(submittedScopes, [
  { provider: 'kommo', pipeline_id: '1', stage_id: '2', channel_id: 'kommo-wa' },
  { provider: 'connect', pipeline_id: '', stage_id: '', channel_id: 'wa-channel-42', instructions: '', allow_group_messages: false },
]);

const groupScopes = JSON.parse(JSON.stringify(first.serialize('connect', ['wa-channel-42'], [], [], true)));
assert.equal(groupScopes[0].allow_group_messages, true,
  'a group response requires an explicit editor confirmation');

// Simulate API/database persistence and a fresh GET /agents response.
const databaseRow = JSON.parse(JSON.stringify({ scopes: submittedScopes }));
const reloaded = editor(databaseRow);
assert.equal(reloaded.elements.get('aiAgentProvider').value, 'connect');
assert.deepEqual(JSON.parse(JSON.stringify(reloaded.context.selectValues('aiChannelId'))), ['wa-channel-42']);

assert.match(source, /provider==='connect'&&!channels\.length/,
  'the editor must refuse to save a Connect agent without an explicit WhatsApp channel');
assert.match(source, /aiScheduleEnabled/,
  'the editor must expose the durable schedule rather than a hidden fixed window');
assert.match(appSource, /data-ai-section-nav="stopping"/,
  'the editor must expose a separate stop/handoff section');
assert.match(fs.readFileSync(path.join(root,'docs/tenant-operator-dialogs.js'),'utf8'), /add\('pause','pause'\)/,
  'an active dialogue must offer Pause rather than only a handoff action');
assert.match(appSource, /id="aiAllowGroupMessages"/,
  'the administrator must explicitly opt in before an AI Agent can handle group messages');

console.log('PASS: Connect provider and selected WhatsApp channel survive editor save/reload flow.');
