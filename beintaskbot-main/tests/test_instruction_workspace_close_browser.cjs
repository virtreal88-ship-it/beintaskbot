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
 async function actionWithDialog(action){const dialogWait=page.waitForEvent('dialog');const actionWait=action();const dialog=await dialogWait;return{dialog,actionWait};}
 try{
  await page.goto('http://127.0.0.1:'+address.port+'/app/instructions?site_lang=ru');
  await page.locator('#newLesson').waitFor({state:'visible'});
  assert.equal(await page.locator('#catalogGlobalNotice').textContent(),'Общий справочник: статьи видны всем компаниям. Изменения в редакторе влияют на общий справочник.');
  const{dialog:guideWarningDialog,actionWait:guideWarningClick}=await actionWithDialog(()=>page.locator('.catalog-card[data-kind="guide"] .catalog-actions button:nth-child(2)').click());
  assert.equal(guideWarningDialog.message(),'Это общий справочник для всех компаний. Изменения будут видны всем. Открыть редактор?');
  await guideWarningDialog.accept();await guideWarningClick;
  const guideFrame=page.frameLocator('#instructionEditorFrame');
  await guideFrame.locator('#new').waitFor({state:'visible'});
  await guideFrame.locator('#close-editor').click();
  await guideFrame.locator('#new').click();
  const guideTitle=guideFrame.locator('#title');
  await guideTitle.fill('Unsaved global guide edit');

  const{dialog:guideX,actionWait:guideXClick}=await actionWithDialog(()=>page.locator('#closeInstructionEditor').click());
  assert.equal(guideX.message(),'Есть несохранённые изменения. Отбросить их и закрыть редактор?');
  await guideX.dismiss();await guideXClick;
  await page.waitForTimeout(30);
  assert.equal(await page.locator('#instructionEditor').evaluate(dialog=>dialog.open),true,'declining Guide close keeps editor open');
  assert.equal(await guideTitle.inputValue(),'Unsaved global guide edit');

  await page.locator('#closeInstructionEditor').focus();
  const{dialog:guideEscape,actionWait:guideEscapePress}=await actionWithDialog(()=>page.keyboard.press('Escape'));
  assert.equal(guideEscape.message(),'Есть несохранённые изменения. Отбросить их и закрыть редактор?');
  await guideEscape.dismiss();await guideEscapePress;
  assert.equal(await page.locator('#instructionEditor').evaluate(dialog=>dialog.open),true,'declining Guide Escape keeps editor open');
  assert.equal(await guideTitle.inputValue(),'Unsaved global guide edit');

  const{dialog:guideDiscard,actionWait:guideDiscardClick}=await actionWithDialog(()=>page.locator('#closeInstructionEditor').click());
  assert.equal(guideDiscard.message(),'Есть несохранённые изменения. Отбросить их и закрыть редактор?');
  await guideDiscard.accept();await guideDiscardClick;
  await page.waitForFunction(()=>!document.querySelector('#instructionEditor').open);
  assert.equal(guidePosts,0,'discarded Guide edits are not posted to the shared CMS');

  const{dialog:reopenGuideWarning,actionWait:reopenGuideClick}=await actionWithDialog(()=>page.locator('.catalog-card[data-kind="guide"] .catalog-actions button:nth-child(2)').click());
  assert.equal(reopenGuideWarning.message(),'Это общий справочник для всех компаний. Изменения будут видны всем. Открыть редактор?');
  await reopenGuideWarning.accept();await reopenGuideClick;
  const uploadFrame=page.frameLocator('#instructionEditorFrame');
  await uploadFrame.locator('#new').waitFor({state:'visible'});
  await uploadFrame.locator('#close-editor').click();
  await uploadFrame.locator('#new').click();
  const photoInput=uploadFrame.locator('#article-photos');
  await photoInput.evaluate(input=>{
   const native=window.createImageBitmap.bind(window);
   window.createImageBitmap=async file=>{await new Promise(resolve=>setTimeout(resolve,900));return native(file)};
  });
  const uploadPromise=photoInput.setInputFiles(path.join(docs,'icon-192.png'));
  await page.waitForFunction(()=>{try{return !!document.querySelector('#instructionEditorFrame').contentWindow.tenantInstructionDirty()}catch{return false}});
  const{dialog:uploadClosePrompt,actionWait:uploadCloseClick}=await actionWithDialog(()=>page.locator('#closeInstructionEditor').click());
  assert.equal(uploadClosePrompt.message(),'Есть несохранённые изменения. Отбросить их и закрыть редактор?','a selected Guide image is dirty while processing');
  await uploadClosePrompt.dismiss();await uploadCloseClick;
  await uploadPromise;
  await uploadFrame.locator('.photo-card img').waitFor({state:'visible'});
  assert.equal(await uploadFrame.locator('.photo-card img').count(),1,'cancelling the close prompt preserves the processed Guide image');
  const closeAfterUpload=await actionWithDialog(()=>page.locator('#closeInstructionEditor').click());
  assert.equal(closeAfterUpload.dialog.message(),'Есть несохранённые изменения. Отбросить их и закрыть редактор?');
  await closeAfterUpload.dialog.accept();await closeAfterUpload.actionWait;
  await page.waitForFunction(()=>!document.querySelector('#instructionEditor').open);
  assert.equal(guidePosts,0,'discarding the unsaved Guide image does not post to the shared CMS');

  const{dialog:retryGuideWarning,actionWait:retryGuideClick}=await actionWithDialog(()=>page.locator('.catalog-card[data-kind="guide"] .catalog-actions button:nth-child(2)').click());
  await retryGuideWarning.accept();await retryGuideClick;
  const failedUploadFrame=page.frameLocator('#instructionEditorFrame');
  await failedUploadFrame.locator('#new').waitFor({state:'visible'});
  await failedUploadFrame.locator('#close-editor').click();
  await failedUploadFrame.locator('#new').click();
  const failedPhotoInput=failedUploadFrame.locator('#article-photos');
  await failedPhotoInput.evaluate(input=>{
   window.createImageBitmap=async()=>{await new Promise(resolve=>setTimeout(resolve,900));throw new Error('synthetic image decode failure')};
  });
  const failedUploadPromise=failedPhotoInput.setInputFiles(path.join(docs,'icon-192.png'));
  await page.waitForFunction(()=>{try{return !!document.querySelector('#instructionEditorFrame').contentWindow.tenantInstructionDirty()}catch{return false}});
  const concurrentTitle=failedUploadFrame.locator('#title');
  await concurrentTitle.fill('Keep this edit after image failure');
  await failedUploadPromise;
  await failedUploadFrame.locator('#modal-status').getByText('synthetic image decode failure').waitFor({state:'visible'});
  const{dialog:failedUploadClosePrompt,actionWait:failedUploadCloseClick}=await actionWithDialog(()=>page.locator('#closeInstructionEditor').click());
  assert.equal(failedUploadClosePrompt.message(),'Есть несохранённые изменения. Отбросить их и закрыть редактор?','parallel form edit remains dirty after image failure');
  await failedUploadClosePrompt.dismiss();await failedUploadCloseClick;
  assert.equal(await concurrentTitle.inputValue(),'Keep this edit after image failure','image failure must preserve the edit made during preparation');
  const closeFailedArticle=await actionWithDialog(()=>page.locator('#closeInstructionEditor').click());
  await closeFailedArticle.dialog.accept();await closeFailedArticle.actionWait;
  await page.waitForFunction(()=>!document.querySelector('#instructionEditor').open);
  assert.equal(guidePosts,0,'failed photo processing and discarded text never post to the shared CMS');

  await page.locator('#newLesson').click();
  const iframe=page.frameLocator('#instructionEditorFrame');
  const title=iframe.locator('.il-lesson-settings input').first();
  await title.waitFor({state:'visible'});
  await title.fill('Unsaved warehouse draft');

  const{dialog:xPrompt,actionWait:xClick}=await actionWithDialog(()=>page.locator('#closeInstructionEditor').click());
  assert.equal(xPrompt.type(),'confirm');
  await xPrompt.dismiss();await xClick;
  await page.waitForTimeout(30);
  assert.equal(await page.locator('#instructionEditor').evaluate(dialog=>dialog.open),true,'declining × close leaves the editor visible');
  assert.equal(await title.inputValue(),'Unsaved warehouse draft','declining close preserves the edited title');

  await page.locator('#closeInstructionEditor').focus();
  const{dialog:escapePrompt,actionWait:escapePress}=await actionWithDialog(()=>page.keyboard.press('Escape'));
  assert.equal(escapePrompt.type(),'confirm');
  await escapePrompt.dismiss();await escapePress;
  await page.waitForTimeout(30);
  assert.equal(await page.locator('#instructionEditor').evaluate(dialog=>dialog.open),true,'declining Escape leaves the editor visible');
  assert.equal(await title.inputValue(),'Unsaved warehouse draft','Escape refusal does not hide or lose edits');

  const reloadPrompts=[];
  const acceptReloadPrompt=dialog=>{reloadPrompts.push(dialog.type());void dialog.accept()};
  page.on('dialog',acceptReloadPrompt);
  const reloadFrame=page.frames().find(frame=>frame.url().includes('lesson_new=1'));
  assert(reloadFrame,'new lesson iframe exists before reload');
  const reloadNavigation=reloadFrame.waitForNavigation({waitUntil:'domcontentloaded'});
  await reloadFrame.evaluate(()=>location.reload()).catch(()=>{});
  await reloadNavigation;
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
  const{dialog:discard,actionWait:discardClick}=await actionWithDialog(()=>page.locator('#closeInstructionEditor').click());
  assert.equal(discard.type(),'confirm');
  await discard.accept();await discardClick;
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
   const{dialog:prompt,actionWait:localClick}=await actionWithDialog(()=>page.locator('#closeInstructionEditor').click());
   assert.equal(prompt.message(),discardText,locale+' exact discard warning');await prompt.dismiss();await localClick;
   assert.equal(await localizedTitle.inputValue(),'Unsaved '+locale+' lesson');
   await page.locator('#closeInstructionEditor').focus();
   const{dialog:escapePrompt,actionWait:localEscape}=await actionWithDialog(()=>page.keyboard.press('Escape'));
   assert.equal(escapePrompt.message(),discardText,locale+' exact Escape warning');await escapePrompt.dismiss();await localEscape;
   assert.equal(await localizedTitle.inputValue(),'Unsaved '+locale+' lesson');
   const{dialog:acceptPrompt,actionWait:localAcceptClick}=await actionWithDialog(()=>page.locator('#closeInstructionEditor').click());
   assert.equal(acceptPrompt.message(),discardText);await acceptPrompt.accept();await localAcceptClick;
   await page.waitForFunction(()=>!document.querySelector('#instructionEditor').open);
  }
  console.log('PASS: Guide editor dirty handshake and cancel/confirm flows; RU/AZ/EN global notices and discard warnings; new lesson recovery');
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(error=>{console.error(error);process.exitCode=1;server.close()});
