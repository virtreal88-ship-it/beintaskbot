// Use the actual mobile disclosure, rather than force-clicking hidden controls.
async function revealAccountControl(page,selector){
 const control=page.locator(selector);await control.waitFor({state:'attached'});
 if(await control.isVisible())return control;
 const avatar=page.locator('.app-avatar-button');
 if(!await avatar.isVisible()){
  const back=page.locator('[data-operator-back]');
  if(await back.isVisible())await back.click();
 }
 await avatar.waitFor({state:'visible'});
 if(await avatar.getAttribute('aria-expanded')!=='true')await avatar.click();
 await control.waitFor({state:'visible'});return control;
}
async function selectAppLanguage(page,value){
 const control=await revealAccountControl(page,'[data-app-language]');await control.selectOption(value);
 const avatar=page.locator('.app-avatar-button');
 if(await avatar.count()&&await avatar.getAttribute('aria-expanded')==='true')await avatar.click();
}
module.exports={revealAccountControl,selectAppLanguage};
