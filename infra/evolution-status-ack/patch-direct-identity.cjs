'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
// Executed entrypoint in the pinned amd64 2.3.7 image (start:prod=node dist/main).
const EXPECTED = '71b49ea87b8d0d7b413b77d067e33a8720c2e95e136f632210c1424862ef6b44';
const hash = source => crypto.createHash('sha256').update(source).digest('hex');
const BEFORE = 'c.key.remoteJid?.includes("@lid")&&c.key.remoteJidAlt&&(c.key.remoteJid=c.key.remoteJidAlt),console.log(c),this.sendDataWebhook("messages.upsert",c)';
// Snapshot only a complete provider-supplied direct LID/PN pair. Evolution's
// internal normalization, chatbot, cache and persistence keep their old object.
// The webhook gets its own key, so deferred serialization cannot lose the LID.
const AFTER = `((message)=>{const key=message.key;const originalKey=typeof key.remoteJid==="string"&&/^[0-9]{5,22}@lid$/.test(key.remoteJid)&&typeof key.remoteJidAlt==="string"&&/^[0-9]{5,22}@s\\.whatsapp\\.net$/.test(key.remoteJidAlt)?{...key}:undefined;key.remoteJid?.includes("@lid")&&key.remoteJidAlt&&(key.remoteJid=key.remoteJidAlt);console.log(message);this.sendDataWebhook("messages.upsert",originalKey?{...message,key:originalKey}:message);})(c)`;
function transform(source) {
  if (hash(source) !== EXPECTED) throw Error('Unrecognized Evolution entrypoint SHA256; refusing to patch');
  if (source.split(BEFORE).length !== 2) throw Error('Expected exactly one identity dispatch anchor');
  return source.replace(BEFORE, AFTER);
}
function patch(root) {
  const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
  if (pkg.version !== '2.3.7' || pkg.scripts?.['start:prod'] !== 'node dist/main') throw Error('Unexpected Evolution version/entrypoint');
  const file = path.join(root, 'dist/main.js');
  const next = transform(fs.readFileSync(file, 'utf8'));
  fs.writeFileSync(file, next);
  console.log(JSON.stringify({package: pkg.name, version: pkg.version, before: EXPECTED, after: hash(next)}));
}
module.exports = {transform, hash, EXPECTED, BEFORE, AFTER};
if (require.main === module) patch(process.argv[2] || '/evolution');
