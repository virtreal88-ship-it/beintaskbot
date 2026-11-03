const { chromium }=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 for(const width of [375,1280]){
  const page=await browser.newPage({viewport:{width,height:800}});
  await page.goto('http://127.0.0.1:18765/',{waitUntil:'networkidle'});
  await page.locator('summary').click();
  const initial=width===375?'Uses Android':'Uses iOS';const target=width===375?'Uses iOS':'Uses Android';
  await page.getByText(initial,{exact:true}).waitFor();
  assert.equal(await page.locator('a').first().getAttribute('href').then(x=>x.includes('source_message_id=')),true);
  await page.getByRole('button',{name:'Исправить'}).click();
  await page.getByRole('textbox',{name:'Факт'}).fill(target);
  await page.getByRole('textbox',{name:'Дословная цитата из выбранного сообщения'}).fill('Synthetic customer statement');
  await page.getByRole('checkbox',{name:'Я проверил, что цитата подтверждает этот факт.'}).check();
  await page.getByRole('button',{name:'Сохранить',exact:true}).click();
  await page.getByText(target,{exact:true}).waitFor();
  assert.equal(await page.getByText('Требует проверки',{exact:false}).count(),1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  await page.close();

 }
 const a=await browser.newPage(),b=await browser.newPage();
 await Promise.all([a.goto('http://127.0.0.1:18765/'),b.goto('http://127.0.0.1:18765/')]);
 await a.locator('summary').click();await b.locator('summary').click();
 await a.getByRole('button',{name:'Исправить'}).click();await a.getByRole('textbox',{name:'Факт'}).fill('Draft preserved');
 await a.getByRole('textbox',{name:'Дословная цитата из выбранного сообщения'}).fill('Synthetic customer statement');
 await a.getByRole('checkbox',{name:'Я проверил, что цитата подтверждает этот факт.'}).check();
 await a.getByRole('button',{name:'Обновить'}).click();
 assert.equal(await a.getByRole('textbox',{name:'Факт'}).inputValue(),'Draft preserved');
 await b.getByRole('button',{name:'Подтвердить'}).click();
 await b.getByText('Подтверждено сотрудником',{exact:false}).waitFor();
 await a.getByRole('button',{name:'Сохранить',exact:true}).click();
 await a.getByRole('button',{name:'Использовать текущую версию'}).waitFor();
 assert.equal(await a.getByRole('textbox',{name:'Факт'}).inputValue(),'Draft preserved');
 await a.getByRole('button',{name:'Использовать текущую версию'}).click();
 await a.getByRole('button',{name:'Сохранить',exact:true}).click();
 await a.getByText('Draft preserved',{exact:true}).waitFor();
 await a.getByRole('button',{name:'Добавить факт'}).click();
 await a.getByRole('combobox',{name:'Выберите сообщение-источник'}).locator('option').first().waitFor({state:'attached'});
 await a.getByRole('combobox',{name:'Категория'}).selectOption('topic');
 await a.getByRole('textbox',{name:'Факт'}).fill('Synthetic reporting preference');
 await a.getByRole('textbox',{name:'Дословная цитата из выбранного сообщения'}).fill('Synthetic customer statement');
 await a.getByRole('checkbox',{name:'Я проверил, что цитата подтверждает этот факт.'}).check();
 await a.getByRole('button',{name:'Сохранить',exact:true}).click();
 await a.getByText('Synthetic reporting preference',{exact:true}).waitFor();
 await a.close();await b.close();
 await browser.close();console.log('PASS: real local HTTP/PostgreSQL, desktop/mobile, draft preservation and version conflict');
})().catch(e=>{console.error(e);process.exitCode=1});
