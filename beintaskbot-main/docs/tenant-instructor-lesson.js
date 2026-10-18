/* Internal lesson player. No customer transport, browser execution or publication. */
(function(global){'use strict';
function transform(image,view,area,padding=50){
 const base=Math.min(view.width/image.width,view.height/image.height,1);
 let scale=base,cx=image.width/2,cy=image.height/2;
 if(area){scale=Math.max(base,Math.min(1,view.width/(area.width+padding*2),view.height/(area.height+padding*2)));cx=area.x+area.width/2;cy=area.y+area.height/2;}
 const clamp=(n,low,high)=>Math.max(low,Math.min(high,n));
 let x=(view.width-image.width*scale)/2,y=(view.height-image.height*scale)/2;
 if(area){if(image.width*scale>view.width)x=clamp(view.width/2-cx*scale,view.width-image.width*scale,0);if(image.height*scale>view.height)y=clamp(view.height/2-cy*scale,view.height-image.height*scale,0);}
 return {scale,x,y};
}
function validate(lesson){
 if(lesson.schema_version!==1||!Array.isArray(lesson.slides)||lesson.slides.length!==8)throw Error('Invalid lesson');
 const ids=new Set();let total=0;
 lesson.slides.forEach((s,i)=>{
  if(s.order!==i+1||ids.has(s.id)||!s.id)throw Error('Invalid order');ids.add(s.id);
  if(!/^[a-zA-Z0-9_-]+\.jpg$/.test(s.image.asset_id)||s.image.width<1||s.image.width>4096||s.image.height<1||s.image.height>4096)throw Error('Invalid asset');
  if(s.playback.duration_ms<4000||s.playback.duration_ms>30000)throw Error('Invalid duration');total+=s.playback.duration_ms;
  for(const a of s.areas){if(![a.x,a.y,a.width,a.height].every(Number.isFinite)||a.x<0||a.y<0||a.width<=0||a.height<=0||a.x+a.width>s.image.width||a.y+a.height>s.image.height)throw Error('Invalid area');}
  for(const f of s.playback.focus){if(!s.areas.some(a=>a.id===f.area_id)||f.at_ms<0||f.at_ms>s.playback.duration_ms-2000)throw Error('Invalid focus');}
 });if(total>144000)throw Error('Lesson too long');return lesson;
}
function mount(root,lesson,options={}){
 validate(lesson);const win=root.ownerDocument.defaultView,doc=root.ownerDocument;
 const clock=options.clock||{now:()=>win.performance.now(),set:(f,t)=>win.setTimeout(f,t),clear:id=>win.clearTimeout(id)};
 const timers=new Set();let index=0,playing=false,elapsed=0,started=0,focus=null,ready=false,dead=false,pendingAuto=false,geometry=null;
 root.classList.add('instructor-lesson');
 root.innerHTML='<header><span class="lesson-tag">BEIN DEMO · AZ</span><h1></h1><p class="lesson-review"></p></header><section aria-label="Dərsin addımı"><p class="lesson-count" aria-live="polite"></p><h2></h2><p class="lesson-explanation"></p><div class="lesson-stage" tabindex="0" aria-label="Real ekran görüntüsü"><div class="lesson-plane"><img alt=""><div class="lesson-areas"></div></div></div><p class="lesson-focus" aria-live="polite"></p><p class="lesson-note"></p></section><nav aria-label="Dərs idarəetməsi"><button type="button" data-back>Əvvəlki</button><button type="button" data-play>Başlat</button><button type="button" data-next>Növbəti</button><span class="lesson-status" aria-live="polite"></span></nav>';
 const $=s=>root.querySelector(s),stage=$('.lesson-stage'),plane=$('.lesson-plane'),img=$('img');
 $('h1').textContent=lesson.title;
 $('.lesson-review').textContent=lesson.review_status==='approved'?'Əməkdaş tərəfindən təsdiqlənib':'Yoxlama üçün layihə · Saxlama və anbar qalıqlarına təsir təsdiqlənməyib';
 const reduced=win.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
 const current=()=>lesson.slides[index];
 const time=()=>elapsed+(playing?clock.now()-started:0);
 const clear=()=>{for(const id of timers)clock.clear(id);timers.clear();};
 function schedule(f,delay){if(delay<0)return;const id=clock.set(()=>{timers.delete(id);if(!dead)f();},delay);timers.add(id);}
 function renderFocus(areaId){focus=areaId;const s=current(),area=s.areas.find(a=>a.id===areaId),frame=s.playback.focus.find(f=>f.area_id===areaId);
  const bounds=stage.getBoundingClientRect();if(!bounds.width||!bounds.height)return;
  plane.style.transition=`transform ${reduced||!ready||(!playing&&!elapsed)?0:s.playback.transition_ms}ms ease-in-out`;
  geometry=transform(s.image,bounds,area,frame?.padding||50);
  plane.style.transform=`translate(${geometry.x}px,${geometry.y}px) scale(${geometry.scale})`;
  $('.lesson-focus').textContent=area?'Vacib sahə avtomatik göstərilir.':'Ümumi görünüş';
 }
 function status(){ $('[data-play]').textContent=playing?'Pauza':(elapsed?'Davam et':'Başlat');$('[data-play]').disabled=!ready;
  $('.lesson-status').textContent=playing?'Avtomatik nümayiş':'Nümayiş dayandırılıb';$('[data-back]').disabled=index===0;$('[data-next]').disabled=index===lesson.slides.length-1;}
 function pause(){pendingAuto=false;if(playing)elapsed=Math.min(time(),current().playback.duration_ms);playing=false;clear();status();}
 function timeline(){clear();const s=current(),t=time();for(const frame of s.playback.focus)if(frame.at_ms>t&&(!frame.only_below_scale||transform(s.image,stage.getBoundingClientRect()).scale<frame.only_below_scale))schedule(()=>renderFocus(frame.area_id),frame.at_ms-t);
  if(s.playback.return_to_overview&&s.playback.focus.length&&s.playback.duration_ms-1800>t)schedule(()=>renderFocus(null),s.playback.duration_ms-1800-t);
  schedule(()=>{if(index===lesson.slides.length-1){pause();elapsed=0;renderFocus(null);status();$('.lesson-status').textContent='Dərs tamamlandı';}else go(index+1,true);},s.playback.duration_ms-t);
 }
 function play(){if(!ready||dead)return;if(playing){pause();return;}playing=true;started=clock.now();timeline();status();}
 function go(next,automatic=false){clear();playing=false;elapsed=0;index=Math.max(0,Math.min(next,lesson.slides.length-1));focus=null;ready=false;pendingAuto=automatic;
  const s=current();$('h2').textContent=s.title;$('.lesson-count').textContent=`Addım ${index+1} / ${lesson.slides.length}`;
  $('.lesson-explanation').textContent=s.explanation;$('.lesson-note').textContent=s.note;
  plane.style.width=s.image.width+'px';plane.style.height=s.image.height+'px';plane.style.transition=`transform ${reduced?0:s.playback.transition_ms}ms ease-in-out`;
  $('.lesson-areas').replaceChildren();for(const a of s.areas){const box=doc.createElement('span');box.className='lesson-area '+(a.intent==='warning'?'warning':'');Object.assign(box.style,{left:a.x+'px',top:a.y+'px',width:a.width+'px',height:a.height+'px'});$('.lesson-areas').append(box);}
  img.alt=`${index+1}. ${s.title} — Bein Demo real ekran görüntüsü`;
  img.onload=()=>{if(img.naturalWidth!==s.image.width||img.naturalHeight!==s.image.height){img.onerror();return;}ready=true;renderFocus(null);status();if(pendingAuto){pendingAuto=false;play();}};
  img.onerror=()=>{ready=false;pause();$('.lesson-status').textContent='Ekran görüntüsü yüklənmədi. Nümayiş dayandırıldı.';};
  const url=(options.resolveAsset||((id)=>id))(s.image.asset_id);const resolved=new URL(url,win.location.href);if(!['http:','https:','blob:'].includes(resolved.protocol)||resolved.origin!==win.location.origin)throw Error('Asset origin forbidden');
  img.src=url;renderFocus(null);status();
 }
 const manual=e=>{if(e.target.closest?.('[data-play]'))return;pause();};
 root.addEventListener('wheel',manual,{passive:true});root.addEventListener('pointerdown',manual);root.addEventListener('keydown',manual);
 $('[data-back]').onclick=()=>go(index-1);$('[data-next]').onclick=()=>go(index+1);$('[data-play]').onclick=play;
 const keys=e=>{if(e.target.matches('input,textarea,select'))return;if(e.key==='ArrowRight'){e.preventDefault();go(index+1);}if(e.key==='ArrowLeft'){e.preventDefault();go(index-1);}};root.addEventListener('keydown',keys);
 const hidden=()=>{if(doc.hidden)pause();};doc.addEventListener('visibilitychange',hidden);
 const resize=()=>renderFocus(focus);win.addEventListener('resize',resize);const observer=win.ResizeObserver?new win.ResizeObserver(resize):null;observer?.observe(stage);
 go(0);
 return {play,pause,go,snapshot:()=>({index,playing,elapsed:time(),focus,ready,geometry}),destroy(){pause();dead=true;img.onload=img.onerror=null;observer?.disconnect();win.removeEventListener('resize',resize);doc.removeEventListener('visibilitychange',hidden);root.removeEventListener('wheel',manual);root.removeEventListener('pointerdown',manual);root.removeEventListener('keydown',manual);root.removeEventListener('keydown',keys);root.replaceChildren();}};
}
global.BeinLesson={mount,validate,transform};if(typeof module!=='undefined')module.exports=global.BeinLesson;
})(typeof window==='undefined'?globalThis:window);
