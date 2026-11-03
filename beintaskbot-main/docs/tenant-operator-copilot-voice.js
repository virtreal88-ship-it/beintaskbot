/* Private Copilot voice remains unavailable until server enforcement ships. */
((root, factory) => {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.mountTenantCopilotVoice = factory;
})(typeof window === 'object' ? window : globalThis, function mountTenantCopilotVoice(parent) {
  if (!parent || parent.querySelector('[data-copilot-voice]')) return null;
  const document=parent.ownerDocument, section=document.createElement('section');
  section.dataset.copilotVoice='';section.hidden=true;
  const button=document.createElement('button');button.type='button';button.disabled=true;
  button.textContent='Голосовой разговор';
  const status=document.createElement('p');status.setAttribute('role','status');
  button.onclick=()=>{status.textContent='Голосовой режим пока недоступен.';};
  section.append(button,status);parent.append(section);
  return section;
});
