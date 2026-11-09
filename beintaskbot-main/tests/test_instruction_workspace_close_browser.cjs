const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');

const docs=path.join(__dirname,'..','docs');
const files={
 '/assets/tenant-instructions.css':'tenant-instructions.css',
 '/assets/tenant-instructor-lesson.css':'tenant-instructor-lesson.css',
 '/assets/tenant-instructor-lesson.js':'tenant-instructor-lesson.js',
 '/assets/tenant-instructions.js':'tenant-instructions.js',
 '/assets/tenant-instruction-catalog.js':'tenant-instruction-catalog.js'
};
const tenant='00000000-0000-4000-8000-000000000031';
const member={tenant_id:tenant,telegram_id:31,role:'owner'};
let guidePosts=0;
const guideArticle={slug:'/ops/global',title:'Global guide',category:'Ops',body:'Start here.',body_format:'html',images:[]};
const guideRow={slug:guideArticle.slug,published:guideArticle,draft:guideArticle};
function json(res,status,body){res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store'});res.end(JSON.stringify(body))}
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://127.0.0.1');
 if(url.pathname==='/app/instructions'){res.writeHead(200,{'content-type':'text/html; charset=utf-8'});return res.end(fs.readFileSync(path.join(docs,'tenant-instructions.html')))}
 if(url.pathname==='/guide-editor'){res.writeHead(200,{'content-type':'text/html; charset=utf-8'});return res.end(fs.readFileSync(path.join(docs,'guide-editor.html')))}
 if(files[url.pathname]){res.writeHead(200,{'content-type':url.pathname.endsWith('.css')?'text/css':'text/javascript','cache-control':'no-store'});return res.end(fs.readFileSync(path.join(docs,files[url.pathname])))}
 if(url.pathname==='/api/platform/me')return json(res,200,{member});
 if(url.pathname==='/api/guides/public')return json(res,200,{success:true,items:[guideArticle]});
 if(url.pathname==='/api/guides'&&req.method==='POST'){guidePosts++;return json(res,200,{success:true,version:2,items:[guideRow]})}
 if(url.pathname==='/api/guides')return json(res,200,{success:true,version:1,items:[guideRow]});
 if(url.pathname==='/api/platform/instruction-catalog')return json(res,200,{success:true,result:{items:[],next_offset:null}});
 if(url.pathname==='/api/platform/instructions')return json(res,200,{success:true,result:{items:[],next_offset:null}});
 if(url.pathname==='/api/platform/ai/agents')return json(res,200,{success:true,result:[]});
 res.writeHead(404);res.end('Not found');
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const address=server.address();
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 const page=await browser.newPage();
 try{
  await page.goto('http://127.0.0.1:'+address.port+'/app/instructions?site_lang=ru');
  await page.locator('#newLesson').waitFor({state:'visible'});
  assert.equal(await page.locator('#catalogGlobalNotice').textContent(),'Общий справочник: статьи видны всем компаниям. Изменения в редакторе влияют на общий справочник.');
  const guideWarning=page.waitForEvent('dialog');
  await page.locator('.catalog-card[data-kind="guide"] .catalog-actions button:nth-child(2)').click();
  const guideWarningDialog=await guideWarning;
  assert.equal(guideWarningDialog.message(),'Это общий справочник для всех компаний. Изменения будут видны всем. Открыть редактор?');
  await guideWarningDialog.accept();
  const guideFrame=page.frameLocator('#instructionEditorFrame');
  await guideFrame.locator('#new').waitFor({state:'visible'});
  await guideFrame.locator('#new').click();
  const guideTitle=guideFrame.locator('#title');
  await guideTitle.fill('Unsaved global guide edit');

  const guideXDialog=page.waitForEvent('dialog');
  await page.locator('#closeInstructionEditor').click();
  const guideX=await guideXDialog;
  assert.equal(guideX.message(),'Есть несохранённые изменения. Отбросить их и закрыть редактор?');
  await guideX.dismiss();
  await page.waitForTimeout(30);
  assert.equal(await page.locator('#instructionEditor').evaluate(dialog=>dialog.open),true,'declining Guide close keeps editor open');
  assert.equal(await guideTitle.inputValue(),'Unsaved global guide edit');

  await page.locator('#closeInstructionEditor').focus();
  const guideEscapeDialog=page.waitForEvent('dialog');
  await page.keyboard.press('Escape');
  const guideEscape=await guideEscapeDialog;
  assert.equal(guideEscape.message(),'Есть несохранённые изменения. Отбросить их и закрыть редактор?');
  await guideEscape.dismiss();
  assert.equal(await page.locator('#instructionEditor').evaluate(dialog=>dialog.open),true,'declining Guide Escape keeps editor open');
  assert.equal(await guideTitle.inputValue(),'Unsaved global guide edit');

  const guideDiscardDialog=page.waitForEvent('dialog');
  await page.locator('#closeInstructionEditor').click();
  const guideDiscard=await guideDiscardDialog;
  assert.equal(guideDiscard.message(),'Есть несохранённые изменения. Отбросить их и закрыть редактор?');
  await guideDiscard.accept();
  await page.waitForFunction(()=>!document.querySelector('#instructionEditor').open);
  assert.equal(guidePosts,0,'discarded Guide edits are not posted to the shared CMS');

  await page.locator('#newLesson').click();
  const iframe=page.frameLocator('#instructionEditorFrame');
  const title=iframe.locator('.il-lesson-settings input').first();
  await title.waitFor({state:'visible'});
  await title.fill('Unsaved warehouse draft');

  const xDialog=page.waitForEvent('dialog');
  await page.locator('#closeInstructionEditor').click();
  const xPrompt=await xDialog;
  assert.equal(xPrompt.type(),'confirm');
  await xPrompt.dismiss();
  await page.waitForTimeout(30);
  assert.equal(await page.locator('#instructionEditor').evaluate(dialog=>dialog.open),true,'declining × close leaves the editor visible');
  assert.equal(await title.inputValue(),'Unsaved warehouse draft','declining close preserves the edited title');

  await page.locator('#closeInstructionEditor').focus();
  const escapeDialog=page.waitForEvent('dialog');
  await page.keyboard.press('Escape');
  const escapePrompt=await escapeDialog;
  assert.equal(escapePrompt.type(),'confirm');
  await escapePrompt.dismiss();
  await page.waitForTimeout(30);
  assert.equal(await page.locator('#instructionEditor').evaluate(dialog=>dialog.open),true,'declining Escape leaves the editor visible');
  assert.equal(await title.inputValue(),'Unsaved warehouse draft','Escape refusal does not hide or lose edits');

  const reloadPrompts=[];
  const acceptReloadPrompt=dialog=>{reloadPrompts.push(dialog.type());void dialog.accept()};
  page.on('dialog',acceptReloadPrompt);
  await iframe.locator('#instructions').evaluate(()=>location.reload()).catch(()=>{});
  await page.waitForFunction(()=>{const frame=document.querySelector('#instructionEditorFrame');try{return frame.contentDocument?.querySelector('.il-lesson-settings input')?.value==='Unsaved warehouse draft'}catch{return false}});
  assert(reloadPrompts.includes('beforeunload'),'reload asks before leaving dirty editor');
  assert(reloadPrompts.includes('confirm'),'lesson_new=1 restores the cached draft after reload');
  assert.equal(await page.frameLocator('#instructionEditorFrame').locator('.il-lesson-settings input').first().inputValue(),'Unsaved warehouse draft');

  const sameEditorFrame=page.frames().find(frame=>frame.url().includes('lesson_new=1'));
  assert(sameEditorFrame,'new lesson editor frame is open');
  await sameEditorFrame.goto('http://127.0.0.1:'+address.port+'/app/instructions?lesson_new=1&site_lang=ru').catch(()=>{});
  await page.frameLocator('#instructionEditorFrame').locator('.il-lesson-settings input').first().waitFor({state:'visible'});
  await page.waitForFunction(()=>{const frame=document.querySelector('#instructionEditorFrame');try{return frame.contentDocument?.querySelector('.il-lesson-settings input')?.value==='Unsaved warehouse draft'}catch{return false}});
  assert(reloadPrompts.filter(type=>type==='beforeunload').length>=2,'closing and reopening a dirty new lesson prompts before leaving');
  assert(reloadPrompts.filter(type=>type==='confirm').length>=2,'reopened lesson restores the latest unsaved draft');

  page.off('dialog',acceptReloadPrompt);
  const discardDialog=page.waitForEvent('dialog');
  await page.locator('#closeInstructionEditor').click();
  const discard=await discardDialog;
  assert.equal(discard.type(),'confirm');
  await discard.accept();
  await page.waitForFunction(()=>!document.querySelector('#instructionEditor').open);
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('instruction-latest:00000000-0000-4000-8000-000000000031:31')),null,'confirmed discard removes the cached draft');
  await page.locator('#newLesson').click();
  await page.frameLocator('#instructionEditorFrame').locator('.il-lesson-settings input').first().waitFor({state:'visible'});
  assert.equal(await page.frameLocator('#instructionEditorFrame').locator('.il-lesson-settings input').first().inputValue(),'','confirmed discard clears the cached unsaved lesson');
  await page.locator('#closeInstructionEditor').click();
  await page.waitForFunction(()=>!document.querySelector('#instructionEditor').open);

  const localized=[
   ['az','Ümumi məlumat bazası: məqalələr bütün şirkətlər üçün görünür. Redaktordakı dəyişikliklər ümumi bazaya təsir edir.','Yadda saxlanmamış dəyişikliklər var. Onları ləğv edib redaktoru bağlamaq istəyirsiniz?'],
   ['en','Shared guide: articles are visible to every company. Changes in the editor affect the shared guide.','There are unsaved changes. Discard them and close the editor?']
  ];
  for(const[locale,guideScope,discardText]of localized){
   await page.goto('http://127.0.0.1:'+address.port+'/app/instructions?site_lang='+locale);
   await page.locator('#newLesson').waitFor({state:'visible'});
   assert.equal(await page.locator('#catalogGlobalNotice').textContent(),guideScope,locale+' global Guide notice');
   await page.locator('#newLesson').click();
   const localizedFrame=page.frameLocator('#instructionEditorFrame'),localizedTitle=localizedFrame.locator('.il-lesson-settings input').first();
   await localizedTitle.waitFor({state:'visible'});await localizedTitle.fill('Unsaved '+locale+' lesson');
   const localDialog=page.waitForEvent('dialog');await page.locator('#closeInstructionEditor').click();const prompt=await localDialog;
   assert.equal(prompt.message(),discardText,locale+' exact discard warning');await prompt.dismiss();
   assert.equal(await localizedTitle.inputValue(),'Unsaved '+locale+' lesson');
   await page.locator('#closeInstructionEditor').focus();
   const escapePromptWait=page.waitForEvent('dialog');await page.keyboard.press('Escape');const escapePrompt=await escapePromptWait;
   assert.equal(escapePrompt.message(),discardText,locale+' exact Escape warning');await escapePrompt.dismiss();
   assert.equal(await localizedTitle.inputValue(),'Unsaved '+locale+' lesson');
   const acceptWait=page.waitForEvent('dialog');await page.locator('#closeInstructionEditor').click();const acceptPrompt=await acceptWait;
   assert.equal(acceptPrompt.message(),discardText);await acceptPrompt.accept();
   await page.waitForFunction(()=>!document.querySelector('#instructionEditor').open);
  }
  console.log('PASS: Guide editor dirty handshake and cancel/confirm flows; RU/AZ/EN global notices and discard warnings; new lesson recovery');
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(error=>{console.error(error);process.exitCode=1;server.close()});
