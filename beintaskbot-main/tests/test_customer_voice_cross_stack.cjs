/* Browser -> real local Worker -> real local Connect -> synthetic provider. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require('playwright');
(async()=>{
  const executablePath=process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':undefined);
  const browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{}),args:['--no-sandbox']});
  try{
    const page=await browser.newPage({viewport:{width:390,height:844}});
    await page.goto(process.env.VOICE_WORKER_FIXTURE_URL+'/app');
    await page.locator('.operator-voice-mic').waitFor({state:'visible'});
    await page.locator('.operator-voice-mic').click();
    await page.getByRole('button',{name:'Остановить'}).waitFor({state:'visible'});
    await page.waitForTimeout(400);await page.getByRole('button',{name:'Остановить'}).click();
    await page.getByRole('button',{name:'Отправить голосовое'}).waitFor({state:'visible'});
    assert.equal(await page.locator('audio').isVisible(),true);
    await page.getByRole('button',{name:'Отправить голосовое'}).click();
    await page.getByText('Провайдер принял сообщение. Доставка клиенту пока не подтверждена.').waitFor({state:'visible',timeout:15000});
    await page.waitForFunction(()=>window.refreshed===true);
    console.log('PASS synthetic browser -> Worker -> Connect -> provider receipt');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
