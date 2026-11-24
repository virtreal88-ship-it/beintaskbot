const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function exposedFunction(source, signature, name) {
    const start = source.indexOf(signature);
    assert(start >= 0, `missing ${name} in current app asset`);
    const end = source.indexOf('\n', start);
    const declaration = source.slice(start, end >= 0 ? end : source.length);
    const context = {};
    vm.runInNewContext(`${declaration}\nthis.formatDate=${name};`, context);
    return context.formatDate;
}

const docs = path.join(__dirname, '../docs');
const linear = exposedFunction(
    fs.readFileSync(path.join(docs, 'tenant-linear-board.js'), 'utf8'),
    'function date(value){', 'date'
);
const newsHtml = fs.readFileSync(path.join(docs, 'news.html'), 'utf8');
const newsStart = newsHtml.indexOf('function formatLinearTimestamp(');
assert(newsStart >= 0, 'public news date formatter remains available');
const newsEnd = newsHtml.indexOf('\n}', newsStart) + 2;
const newsContext = {};
vm.runInNewContext(`${newsHtml.slice(newsStart, newsEnd)}\nthis.formatDate=formatLinearTimestamp;`, newsContext);

for (const format of [linear, newsContext.formatDate]) {
    assert.equal(format('2026-10-06T12:46:29Z'), '06/10/2026 16:46:29');
    assert.equal(format('2026-10-05T20:00:00Z'), '06/10/2026 00:00:00');
    assert.equal(format('invalid'), '—');
    assert.equal(format(null), '—');
}
console.log('Current app Linear and public news dates use readable Baku timestamps.');
