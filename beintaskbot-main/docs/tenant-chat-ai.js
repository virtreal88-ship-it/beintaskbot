/* AI results remain editable drafts. Exact request survives uncertain network replies. */
(() => {
  const $=id=>document.getElementById(id);let session,epoch=0,lead=0,pending=null,busy=false;
  const storageKey='tenant-chat-ai-pending';
  document.head.insertAdjacentHTML('beforeend','<style>#dealModal .modal-card{max-height:calc(100dvh - 40px);overflow:auto}#dealModal .modal-head{position:sticky;top:0;background:#fff;z-index:1;padding-bottom:12px}.tc-ai-spinner{display:inline-block;width:13px;height:13px;border:2px solid #c5d8d1;border-top-color:#087d5e;border-radius:50%;animation:tc-ai-spin .8s linear infinite;margin-right:7px}@keyframes tc-ai-spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.tc-ai-spinner{animation:none}}</style>');
  function identity(member){return member?.tenant_id+':'+member?.telegram_id;}
  function grow(input){input.style.height='auto';input.style.height=input.scrollHeight+'px';}
  function clear(){pending=null;sessionStorage.removeItem(storageKey);}
  function remember(data){pending=data;sessionStorage.setItem(storageKey,JSON.stringify(data));}
  function install(data){if(identity(session?.member)!==identity(data.member)){epoch++;busy=false;pending=null;lead=0;$('tcAiResult')?.remove();$('tcAiActions')?.remove();}else if(session?.capabilities?.chat_ai&&!data.capabilities?.chat_ai){epoch++;busy=false;$('tcAiResult')?.remove();$('tcAiActions')?.remove();}session=data;
    try{const stored=JSON.parse(sessionStorage.getItem(storageKey)||'null');if(stored&&stored.expected_tenant_id===data.member.tenant_id&&String(stored.expected_user_id)===String(data.member.telegram_id))pending=stored;else clear();}catch{clear();}if(lead)render();}
  function render(){ $('tcAiActions')?.remove();if(!session?.capabilities?.chat_ai||!$('chatText'))return;
    const actions=document.createElement('div');actions.id='tcAiActions';actions.style='display:flex;gap:8px;flex-wrap:wrap;margin-top:10px';
    for(const [mode,label] of [['reply','✦ AI cavab'],['summary','✦ Xülasə']]){const button=document.createElement('button');button.type='button';button.className='outline';button.textContent=label;button.onclick=()=>request(mode);actions.append(button);}
    if(pending){const retry=document.createElement('button');retry.className='outline';retry.textContent='Əvvəlki sorğunu yoxla';retry.onclick=()=>execute(pending);const discard=document.createElement('button');discard.className='outline';discard.textContent='Əvvəlki sorğunu bağla';discard.onclick=()=>{if(busy)return;if(confirm('Əvvəlki sorğu hələ emal oluna və pullu ola bilər. Onu yenidən icra etmədən bağlamaq istəyirsiniz? Yeni sorğu ayrıca ödəniş yarada bilər.')){clear();render();}};actions.append(retry,discard);}
    $('chatText').parentElement.after(actions);$('chatText').oninput=()=>grow($('chatText'));
    $('chatRows')?.querySelectorAll('[data-chat-audio]').forEach(node=>{if(node.querySelector('[data-transcribe]'))return;const button=document.createElement('button');button.type='button';button.className='outline';button.dataset.transcribe='1';button.textContent='Mətnə çevir';button.onclick=()=>request('transcribe',node.dataset.chatAudio);node.append(button);});
  }
  function show(result){$('tcAiResult')?.remove();const box=document.createElement('section');box.id='tcAiResult';box.className='panel';box.style='margin-top:12px';const title=document.createElement('b');title.textContent=result.mode==='summary'?'Dialoq xülasəsi':result.mode==='transcribe'?'Səsin mətni':'Cavab qaralaması';const text=document.createElement('textarea');text.value=result.text;text.rows=3;text.style='width:100%;box-sizing:border-box;resize:none';text.oninput=()=>grow(text);
    const warning=document.createElement('p');warning.textContent=result.missing_audio?`${result.missing_audio} səs yazısı emal olunmadı. Nəticə tam tarixçəni əhatə etmir.`:`${result.message_count||1} mesaj · ${result.transcribed_audio||0} səs`;
    const insert=document.createElement('button');insert.className='outline';insert.textContent='Mesaj sahəsinə köçür';insert.onclick=()=>{if($('chatText')){$('chatText').value=text.value;grow($('chatText'));}};const close=document.createElement('button');close.className='outline';close.textContent='Bağla';close.onclick=()=>box.remove();box.append(title,text,warning,insert,close);$('chatText').parentElement.after(box);grow(text);
  }
  async function request(mode,message=''){if(busy)return;if(pending)return alert('Əvvəlki sorğunu əvvəlcə yoxlayın. Yeni pullu sorğu yaradılmır.');remember({request_id:crypto.randomUUID(),lead_id:Number(lead),mode,message_id:message,draft:$('chatText')?.value||'',expected_tenant_id:session.member.tenant_id,expected_user_id:session.member.telegram_id});await execute(pending);}
  async function execute(data){if(busy)return;const current=epoch,selected=lead;busy=true;const buttons=[...($('modalContent')?.querySelectorAll('button')||[])];buttons.forEach(b=>b.disabled=true);if($('tcAiActions'))$('tcAiActions').setAttribute('aria-busy','true');
    const notice=document.createElement('p');const spinner=document.createElement('span');spinner.className='tc-ai-spinner';spinner.setAttribute('aria-hidden','true');notice.append(spinner,document.createTextNode('AI işləyir… Səs yazıları olduqda daha uzun çəkə bilər.'));notice.setAttribute('role','status');$('tcAiActions')?.append(notice);
    try{const response=await fetch('/api/platform/chat/ai',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await response.json();if(current!==epoch)return;if(!response.ok||!result.success){if([400,403,404,409,429].includes(response.status)&&!result.keep_request)clear();throw new Error(result.error||'AI cavabı alınmadı.');}clear();if(selected===lead&&Number(data.lead_id)===Number(lead))show(result);}
    catch(error){if(current===epoch)alert(error.message+' Sorğu kodu: '+data.request_id);}finally{if(current===epoch){busy=false;buttons.forEach(b=>b.disabled=false);notice.remove();render();}}
  }
  document.addEventListener('tenant-profile',event=>install(event.detail));
  document.addEventListener('tenant-chat-open',event=>{epoch++;busy=false;lead=event.detail.lead_id;$('tcAiResult')?.remove();render();});
  document.addEventListener('tenant-chat-loaded',event=>{if(Number(event.detail.lead_id)===Number(lead))render();});
  document.addEventListener('tenant-chat-history-added',event=>{if(Number(event.detail.lead_id)===Number(lead))render();});
  const initial=epoch;fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(initial===epoch&&data?.member)install(data);}).catch(()=>{});
})();
