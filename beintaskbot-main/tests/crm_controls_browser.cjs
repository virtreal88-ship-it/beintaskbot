// Exercise the real mobile disclosure; desktop/standalone fixtures remain direct.
async function revealCrmControl(page,selector){
 const control=page.locator(selector);await control.waitFor({state:'attached'});
 const focusSearch=async()=>{if(await control.evaluate(node=>node.matches('input[type=search]')))await control.focus();return control;};
 if(await control.isVisible())return focusSearch();
 const trigger=await control.evaluate(el=>{const panel=el.closest('.crm-mobile-popover');if(!panel)return null;return '#'+panel.closest('.view').id+' .crm-mobile-'+(panel.classList.contains('crm-mobile-filter-panel')?'filter':'more')});
 if(trigger)await page.locator(trigger).click();
 await control.waitFor({state:'visible'});return focusSearch();
}
module.exports={revealCrmControl};
