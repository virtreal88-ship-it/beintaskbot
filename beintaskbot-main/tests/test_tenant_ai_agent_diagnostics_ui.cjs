const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '..', 'docs', 'tenant-ai-agents.js'), 'utf8');
assert.match(source, /error\.status=response\.status/,
  'API errors must preserve their real HTTP status');
assert.match(source, /Не удалось получить диагностику\$\{status\}/,
  'the diagnostic button must show an explicit failure and HTTP status');
assert.match(source, /Сервер вернул неполный ответ диагностики; состояния не показаны/,
  'malformed successful responses must not be rendered as false or zero');
assert.doesNotMatch(source, /state=result\.system\|\|\{\},flags=result\.server_flags\|\|\{\}/,
  'missing diagnostic data must not silently default to empty state');

console.log('PASS: diagnostic failures preserve HTTP status and malformed payloads fail closed.');
