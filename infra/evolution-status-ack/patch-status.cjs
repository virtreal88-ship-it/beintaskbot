'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const EXPECTED = '8b9329ddf95fa7a1785826d8861af236f5f055ff22a5ed1ce52afdf9565d873e';
const hash = s => crypto.createHash('sha256').update(s).digest('hex');
function transform(source) {
  if (hash(source) !== EXPECTED) throw Error('Unrecognized Baileys source SHA256; refusing to patch');
  const replace = (old, next) => {
    if (source.split(old).length !== 2) throw Error('Expected exactly one patch anchor');
    source = source.replace(old, next);
  };
  replace('    const makeOfflineNodeProcessor = () => {', `    // Backport of WhiskeySockets/Baileys#2883 for the pinned rc.9 image.
    // Plain ACK only: a NACK leaves the status pending on the server.
    const handleStatus = async (node) => {
        try {
            await sendMessageAck(node);
        }
        catch (error) {
            onUnexpectedError(error, 'acking status stanza');
        }
    };
    const makeOfflineNodeProcessor = () => {`);
  replace("            ['notification', handleNotification]\n", "            ['notification', handleNotification],\n            ['status', handleStatus]\n");
  replace("    ws.on('CB:ack,class:message', (node) => {", `    ws.on('CB:status', async (node) => {
        await processNode('status', node, 'acking status stanza', handleStatus)
            .catch(error => onUnexpectedError(error, 'acking status stanza'));
    });
    ws.on('CB:ack,class:message', (node) => {`);
  return source;
}
function patch(root) {
  const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
  if (pkg.name !== 'baileys' || pkg.version !== '7.0.0-rc.9') throw Error('Unexpected Baileys package/version');
  const file = path.join(root, 'lib/Socket/messages-recv.js');
  const next = transform(fs.readFileSync(file, 'utf8'));
  fs.writeFileSync(file, next);
  console.log(JSON.stringify({package: pkg.name, version: pkg.version, before: EXPECTED, after: hash(next)}));
}
module.exports = {transform, hash, EXPECTED};
if (require.main === module) patch(process.argv[2] || '/evolution/node_modules/baileys');
