// Local Chromium fake-media test; no provider or user data, no network except
// the loopback fixture server. Optional dependency: sibling held PR helper.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const http=require('node:http');
const path=require('node:path');
const {chromium}=require('playwright');
const helper=process.env.VOICE_MICROPHONE_HELPER||'/workspace/voice-beintaskbot/beintaskbot-main/docs/tenant-microphone-lease.js';

test('Chromium fake microphone is exclusive across two tabs and stops',async()=>{
  const server=http.createServer((_req,res)=>{res.writeHead(200,{'Content-Type':'text/html'});res.end('<!doctype html><body><main></main></body>');});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try {
    const origin='http://127.0.0.1:'+server.address().port;
    browser=await chromium.launch({headless:true,executablePath:'/usr/bin/chromium',args:['--no-sandbox','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']});
    const context=await browser.newContext({permissions:['microphone']});
    const first=await context.newPage(),second=await context.newPage();
    for(const page of [first,second]){
      await page.goto(origin);
      await page.addScriptTag({path:helper});
      await page.addScriptTag({path:path.join(__dirname,'audio.js')});
      await page.addScriptTag({path:path.join(__dirname,'browser.js')});
      await page.evaluate(()=>{
        const old=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia=async(...args)=>{const stream=await old(...args);window.testStream=stream;return stream;};
        window.sent=0;window.controls=[];
        window.view=mountExperimentalCopilotVoice(document.querySelector('main'),{
          acquireMicrophone:acquireTenantMicrophone,openAudio:openExperimentalCopilotAudio,
          connect:async handler=>{window.onVoiceEvent=handler;return {send:event=>{window.sent++;if(event.type!=='audio')window.controls.push(event);},close:async()=>{}};}});
      });
    }
    await first.evaluate(()=>window.view.start());
    await first.waitForFunction(()=>document.querySelector('[role=status]').textContent==='Слушаю');
    await first.waitForFunction(()=>window.sent>0);
    await first.locator('button').nth(1).click();
    assert.equal(await first.evaluate(()=>window.testStream.getAudioTracks()[0].enabled),false);
    await first.locator('button').nth(1).click();
    assert.equal(await first.evaluate(()=>window.testStream.getAudioTracks()[0].enabled),true);
    await first.evaluate(async()=>{await window.onVoiceEvent({type:'audio',item_id:'synthetic',pcm:new Uint8Array(4800)});
      await window.onVoiceEvent({type:'interrupt',item_id:'synthetic'});});
    assert.deepEqual(await first.evaluate(()=>window.controls.at(-1)),{type:'played',item_id:'synthetic',played_ms:0});
    await second.evaluate(()=>window.view.start());
    assert.match(await second.locator('[role=status]').innerText(),/другом режиме/);
    await first.evaluate(()=>window.view.stop());
    assert.equal(await first.evaluate(()=>window.testStream.getTracks()[0].readyState),'ended');
    await second.evaluate(()=>window.view.start());
    await second.waitForFunction(()=>document.querySelector('[role=status]').textContent==='Слушаю');
    await second.evaluate(()=>window.view.stop());
    assert.equal(await second.evaluate(()=>window.testStream.getTracks()[0].readyState),'ended');
    await context.close();
  } finally {
    if(browser)await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
});
