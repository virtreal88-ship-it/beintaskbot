const {chromium}=require('../.support-test-deps/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(require('node:fs').existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{
 const page=await browser.newPage({viewport:{width:390,height:844}});page.on('dialog',()=>assert.fail('No native dialog'));
 await page.goto(process.argv[2]);await page.locator('#dialogConsultationStart').click();
 await page.getByRole('dialog').getByRole('combobox').first().waitFor();
 await page.getByRole('dialog').getByText('Закрыть',{exact:true}).click();
 assert.equal((await (await page.request.get(process.argv[2]+'stats')).json()).created,0);
 await page.locator('#dialogConsultationStart').click();
 await page.getByRole('dialog').getByRole('combobox').first().waitFor();
 assert.doesNotMatch(await page.getByRole('dialog').getByRole('combobox').first().textContent(),/@g\.us/);
 await page.getByRole('dialog').getByRole('textbox').fill('Synthetic browser consultation');
 await page.getByRole('dialog').getByRole('button',{name:/Отправить выбранному/}).click();
 await page.getByRole('dialog').getByText(/Connect принял в очередь/).waitFor();
 await page.getByRole('dialog').getByText('Закрыть',{exact:true}).click();
 await page.request.post(process.argv[2]+'receipt');
 await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready')));
 await page.locator('#operatorMoreActions summary').click();
 await page.locator('#dialogConsultationMobileBell').click();
 await page.locator('#dialogConsultationMenu').getByText('Synthetic developer reply',{exact:true}).waitFor();
 assert.equal(await page.locator('.app-topbar').isVisible(),false);
 await page.locator('#dialogConsultationMenu').getByRole('button',{name:'Закрыть',exact:true}).click();
 assert.equal(await page.locator('#dialogConsultationMenu').isVisible(),false);
 const stats=await (await page.request.get(process.argv[2]+'stats')).json();assert.deepEqual(stats,{created:1,sends:1,tickets:0});
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
