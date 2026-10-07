const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
for (const file of ['index.html', 'news.html']) {
    const html = fs.readFileSync(path.join(__dirname, '../docs', file), 'utf8');
    const start = html.indexOf('function formatLinearTimestamp(');
    const end = html.indexOf("\n}", start);
    const functionEnd = end >= 0 ? end + 2 : html.indexOf("\n    }", start) + 6;
    const ctx = {};
    vm.runInNewContext(html.slice(start, functionEnd), ctx);
    assert.equal(ctx.formatLinearTimestamp('2026-10-06T12:46:29Z'), '06/10/2026 16:46:29');
    assert.equal(ctx.formatLinearTimestamp('2026-10-05T20:00:00Z'), '06/10/2026 00:00:00');
    assert.equal(ctx.formatLinearTimestamp('invalid'), '—');
    assert.equal(ctx.formatLinearTimestamp(null), '—');
}
console.log('Linear and news dates use readable Baku timestamps');
