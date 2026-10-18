const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');

const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-support-inbox.js'),'utf8');

assert.match(source,/function displayName\(ticket\)\{return ticket\.contact_name\|\|ticket\.phone\|\|t\('customer'\);\}/,
  'support display names must use only the API-approved name, phone, and localized fallback');
assert.match(source,/esc\(displayName\(ticket\)\)/,
  'both list and detail must render the normalized display name');
assert.doesNotMatch(source,/contact_name\|\|ticket\.phone\|\|ticket\.conversation_id/,
  'a JID/conversation identifier must never become the visible support name');

console.log('PASS: support inbox uses contact_name, then phone, then a localized fallback without exposing conversation IDs.');
