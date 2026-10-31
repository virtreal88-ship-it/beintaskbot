const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const html=fs.readFileSync(path.join(__dirname,'../../docs/public-case.html'),'utf8');
const fixture={selected_media_ids:['im','vid','aud','doc'],public_messages:[
 {id:'text',speaker:'Müştəri',text:'<img src=x onerror=alert(1)> complaint',type:'text'},
 {id:'im',speaker:'Müştəri',text:'Image attached',type:'image'},
 {id:'vid',speaker:'Müştəri',text:'Video attached',type:'video'},
 {id:'aud',speaker:'Müştəri',text:'Audio attached',type:'audio'},
 {id:'doc',speaker:'Müştəri',text:'Document attached',type:'document'}]};
const dom=new JSDOM(html.replace('__CASE_DATA__',JSON.stringify(fixture)),{url:'https://example.test/public/case/synthetic-token',runScripts:'outside-only'});
const script=[...dom.window.document.scripts].map(node=>node.textContent).join('\n');
dom.window.eval(script);
const doc=dom.window.document;
assert.equal(doc.querySelectorAll('form').length,0,'public page is read only');
assert.equal(doc.querySelectorAll('#thread img').length,1,'untrusted text cannot inject another image');
assert.equal(doc.querySelector('#thread img').alt,'Müraciətə əlavə edilmiş şəkil');
assert.equal(doc.querySelector('video').controls,true);
assert.equal(doc.querySelector('audio').controls,true);
assert.equal(doc.querySelector('#thread a').textContent,'Əlavə edilmiş sənədi aç');
const media=[...doc.querySelectorAll('#thread img,#thread video,#thread audio,#thread a')];assert.equal(media.length,4);
assert.ok(media.every(node=>node.src||node.href));
assert.ok(doc.querySelector('#thread').textContent.includes('<img src=x onerror=alert(1)> complaint'));
assert.ok(!doc.querySelector('#thread img[onerror]'));
assert.ok(doc.querySelector('meta[name=referrer]').content==='no-referrer');
dom.window.close();
console.log('PASS: synthetic public-case fixture renders image/video/audio/document, escapes message HTML, and offers no write form.');
