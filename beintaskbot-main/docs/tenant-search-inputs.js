/* Search-only autofill hints. Never inspect saved credentials or erase queries.
 * Browsers/extensions can ignore autocomplete=off; these are defence-in-depth
 * hints, not a guarantee against an explicitly invoked password manager.
 */
(()=>{
  'use strict';
  const selector='input[type="search"],input[data-search-input]';
  const managed=new WeakSet(),armed=new WeakSet();
  const credentialTokens=/\b(username|current-password|new-password|one-time-code)\b/;
  function eligible(input){
    if(!(input instanceof HTMLInputElement)||!input.matches(selector))return false;
    if(['password','email','hidden'].includes(input.type)||credentialTokens.test(input.autocomplete))return false;
    // Do not change login/account forms, even if a widget uses type=search.
    return !input.form?.querySelector('input[type="password"],input[autocomplete="username"],input[autocomplete="one-time-code"]');
  }
  function prepare(input){
    if(managed.has(input)||!eligible(input))return;
    managed.add(input);
    input.autocomplete='off';
    input.setAttribute('data-lpignore','true');
    input.setAttribute('data-1p-ignore','true');
    input.setAttribute('data-bwignore','true');
    input.setAttribute('data-form-type','other');
    input.setAttribute('autocapitalize','none');
    input.setAttribute('autocorrect','off');
    input.spellcheck=false;
    // Keep existing names/form serialization, validation and authored readonly.
    // Readonly suppresses HTML constraint validation, so constrained queries
    // (e.g. required phone lookup) receive hints only.
    // Idle searches are not credential targets; pointer/focus releases before
    // typing, paste, IME or the mobile keyboard. Query values are never touched.
    if(!input.readOnly&&!input.required&&input.minLength<0&&!input.pattern){
      armed.add(input);
      input.readOnly=document.activeElement!==input;
    }
  }
  function scan(root){
    if(root.nodeType!==1&&root.nodeType!==9)return;
    if(root.matches?.(selector))prepare(root);
    root.querySelectorAll(selector).forEach(prepare);
  }
  function release(event){
    const input=event.target.closest?.('label')?.control||event.target;
    if(eligible(input)){prepare(input);if(armed.has(input))input.readOnly=false;}
  }
  document.addEventListener('pointerdown',release,true);
  document.addEventListener('focusin',release,true);
  document.addEventListener('focusout',event=>{
    const input=event.target;
    if(armed.has(input)&&eligible(input))input.readOnly=true;
  },true);
  scan(document);
  new MutationObserver(records=>{
    for(const record of records){
      if(record.type==='attributes')scan(record.target);
      else record.addedNodes.forEach(scan);
    }
  }).observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['type','data-search-input']});
})();
