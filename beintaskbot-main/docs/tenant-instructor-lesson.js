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
 validate(lesson);const silentLesson=lesson;let voice=!!options.narration,chosenRate=false;
 const voicedLesson=voice?JSON.parse(JSON.stringify(lesson)):null;
 if(voice){if(options.narration.slides.length!==8)throw Error("Invalid narration");voicedLesson.slides.forEach((slide,i)=>{const n=options.narration.slides[i];if(!/^[a-z0-9-]+\.mp3$/.test(n.asset_id)||!Number.isFinite(n.duration_ms)||n.duration_ms<1000||n.duration_ms>25000)throw Error("Invalid audio");slide.playback.duration_ms=n.duration_ms+1000;slide.playback.focus=slide.playback.focus.map((f,j)=>({...f,at_ms:Math.min(n.cues?.[j]??f.at_ms,n.duration_ms-1200)}));});validate(voicedLesson);lesson=voicedLesson;}
 const win=root.ownerDocument.defaultView,doc=root.ownerDocument;
 const clock=options.clock||{now:()=>win.performance.now(),set:(f,t)=>win.setTimeout(f,t),clear:id=>win.clearTimeout(id)};
 let offsets,total;function timing(){offsets=[0];for(const s of lesson.slides)offsets.push(offsets.at(-1)+s.playback.duration_ms);total=offsets.at(-1);}timing();
 const rates=[.75,1,1.25,1.5,1.75,2];
 let index=-1,position=0,started=0,rate=voice?1:1.75,playing=false,ready=false,dead=false,timer=null,geometry=null,focus=null;
 root.classList.add('instructor-lesson');
 root.innerHTML='<header><span class="lesson-tag">BEIN DEMO · AZ</span><h1></h1><p class="lesson-review"></p></header><section aria-label="Dərsin addımı"><p class="lesson-count" aria-live="polite"></p><h2></h2><p class="lesson-explanation"></p><div class="lesson-stage" tabindex="0" aria-label="Real ekran görüntüsü"><div class="lesson-plane"><img alt=""><div class="lesson-areas"></div></div></div><p class="lesson-focus"></p><p class="lesson-note"></p></section><nav aria-label="Dərs idarəetməsi"><button type="button" data-play aria-label="Başlat"><span aria-hidden="true">▶</span></button><button type="button" data-sound aria-label="Səsi söndür" title="Səsi söndür">♪</button><div class="lesson-timeline"><input data-seek type="range" min="0" step="10" aria-label="Dərsin vaxtı"><span class="lesson-time"><time data-current>0:00</time><span aria-hidden="true"> / </span><time data-total></time></span></div><label class="lesson-speed"><select data-speed aria-label="Nümayiş sürəti"><option value="0.75">0.75×</option><option value="1">1×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="1.75" selected>1.75×</option><option value="2">2×</option></select></label><span class="lesson-status" aria-live="polite"></span></nav>';
 const $=s=>root.querySelector(s),stage=$('.lesson-stage'),plane=$('.lesson-plane'),img=$('img'),seekbar=$('[data-seek]');
 let outgoing=null;const preloads=new Map(),fadeMs=650;
 function asset(id){const url=(options.resolveAsset||((id)=>id))(id),resolved=new URL(url,win.location.href);if(!['http:','https:','blob:'].includes(resolved.protocol)||resolved.origin!==win.location.origin)throw Error('Asset origin forbidden');return url;}
 function preload(next){if(!lesson.slides[next]||preloads.has(next))return;const image=new win.Image();image.src=asset(lesson.slides[next].image.asset_id);preloads.set(next,image);image.decode?.().catch(()=>{});}
 function clearOutgoing(){outgoing?.remove();outgoing=null;}
 const audioCache=new Map();let audio=null,tail=false,audioTimeout=null,audioGeneration=0;
 function audioFor(i){if(!audioCache.has(i)){const n=options.narration.slides[i],a=options.audioFactory?options.audioFactory():new win.Audio();const url=options.resolveAudio(n.asset_id),u=new URL(url,win.location.href);if(!['http:','https:','blob:'].includes(u.protocol)||u.origin!==win.location.origin)throw Error('Audio origin forbidden');a.preload='auto';a.src=url;a.preservesPitch=true;audioCache.set(i,a);}return audioCache.get(i);}
 function cancelAudio(){audioGeneration++;if(audioTimeout!==null)clock.clear(audioTimeout);audioTimeout=null;audio?.pause();}
 function audioFailure(){pause();$('.lesson-status').classList.add('lesson-error');$('.lesson-status').textContent='Səs yüklənmədi. Səsi söndürərək davam edə bilərsiniz.';}
 function syncAudio(){cancelAudio();audio=null;if(!voice||index<0)return;audio=audioFor(index);const local=Math.max(0,position-offsets[index]),duration=options.narration.slides[index].duration_ms;tail=local>=duration;
  audio.currentTime=Math.min(local,duration)/1000;audio.playbackRate=rate;
  const active=audio;active.onended=()=>{if(!voice||audio!==active||!playing)return;position=offsets[index]+duration;started=clock.now();tail=true;tick();};active.onerror=()=>{if(voice&&audio===active)audioFailure();};active.onwaiting=()=>{if(playing&&voice&&audio===active){if(audioTimeout!==null)clock.clear(audioTimeout);audioTimeout=clock.set(audioFailure,8000);}};active.onplaying=()=>{if(audio!==active)return;if(audioTimeout!==null)clock.clear(audioTimeout);audioTimeout=null;};
  if(!tail&&playing&&ready){const generation=audioGeneration;audioTimeout=clock.set(()=>{if(generation===audioGeneration)audioFailure();},8000);Promise.resolve(audio.play()).then(()=>{if(generation!==audioGeneration)return;if(audioTimeout!==null)clock.clear(audioTimeout);audioTimeout=null;}).catch(()=>{if(generation===audioGeneration)audioFailure();});}
  if(index<7)audioFor(index+1);
 }
 const motion=win.matchMedia?.('(prefers-reduced-motion: reduce)');
 const format=t=>{const n=Math.floor(t/1000);return Math.floor(n/60)+':'+String(n%60).padStart(2,'0');};
 $('h1').textContent=lesson.title;
 $('.lesson-review').textContent=lesson.review_status==='approved'?'Əməkdaş tərəfindən təsdiqlənib':'Yoxlama üçün layihə · Saxlama və anbar qalıqlarına təsir təsdiqlənməyib';
 seekbar.max=total;$('[data-total]').textContent=format(total);
 const time=()=>{if(voice&&playing&&ready&&!tail)return Math.min(offsets[index]+options.narration.slides[index].duration_ms,offsets[index]+(audio?.currentTime||0)*1000);return Math.min(total,position+(playing&&ready?(clock.now()-started)*rate:0));};
 const clear=()=>{if(timer!==null)clock.clear(timer);timer=null;};
 function status(){
  const label=playing?'Pauza':position>=total?'Yenidən başlat':position?'Davam et':'Başlat';
  $('[data-play] span').textContent=playing?'Ⅱ':'▶';$('[data-play]').setAttribute('aria-label',label);$('[data-play]').title=label;$('[data-play]').disabled=!ready&&!playing;
  $('[data-play]').setAttribute('aria-pressed',String(playing));
  $('[data-sound]').hidden=!options.narration;$('[data-sound]').textContent=voice?'♪':'♩';$('[data-sound]').setAttribute('aria-pressed',String(voice));$('[data-sound]').setAttribute('aria-label',voice?'Səsi söndür':'Səsi aç');$('[data-sound]').title=voice?'Səsi söndür':'Səsi aç';$('[data-speed]').value=rate;
  $('.lesson-status').textContent=position>=total?'Dərs tamamlandı':playing?'Avtomatik nümayiş':'Nümayiş dayandırılıb';

 }
 function loadSlide(next,automatic=false){
  cancelAudio();clearOutgoing();
  if(automatic&&ready&&!motion?.matches){outgoing=plane.cloneNode(true);outgoing.className="lesson-outgoing";outgoing.setAttribute("aria-hidden","true");outgoing.style.opacity="1";stage.append(outgoing);}

  index=next;ready=false;$('.lesson-status').classList.remove('lesson-error');const s=lesson.slides[index];
  plane.style.width=s.image.width+'px';plane.style.height=s.image.height+'px';
  $('.lesson-areas').replaceChildren();for(const a of s.areas){const box=doc.createElement('span');box.className='lesson-area '+(a.intent==='warning'?'warning':'');Object.assign(box.style,{left:a.x+'px',top:a.y+'px',width:a.width+'px',height:a.height+'px'});$('.lesson-areas').append(box);}
  img.alt=`${index+1}. ${s.title} — Bein Demo real ekran görüntüsü`;
  img.onload=()=>{if(dead||index!==next)return;if(img.naturalWidth!==s.image.width||img.naturalHeight!==s.image.height){img.onerror();return;}ready=true;started=clock.now();  $('h2').textContent=s.title;$('.lesson-count').textContent=`Addım ${index+1} / ${lesson.slides.length}`;
  $('.lesson-explanation').textContent=s.explanation;$('.lesson-note').textContent=s.note;
preload(next+1);syncAudio();render();status();tick();};
  img.onerror=()=>{ready=false;pause();$('.lesson-status').classList.add('lesson-error');$('.lesson-status').textContent='Ekran görüntüsü yüklənmədi. Nümayiş dayandırıldı.';};
  img.src=asset(s.image.asset_id);
 }
 function render(){
  const t=time();seekbar.value=t;$('[data-current]').textContent=format(t);seekbar.setAttribute('aria-valuetext',format(t)+' / '+format(total));
  const s=lesson.slides[index],local=t-offsets[index],bounds=stage.getBoundingClientRect();if(!bounds.width||!bounds.height)return;
  let g=transform(s.image,bounds),active=null;
  const frames=s.playback.focus.filter(f=>!f.only_below_scale||g.scale<f.only_below_scale).map(f=>({...f,area:s.areas.find(a=>a.id===f.area_id)}));
  if(s.playback.return_to_overview)frames.push({at_ms:s.playback.duration_ms-1000,area:null});
  for(const frame of frames){if(local<frame.at_ms)break;
   const target=transform(s.image,bounds,frame.area,frame.padding??50),fraction=motion?.matches?1:Math.min(1,(local-frame.at_ms)/s.playback.transition_ms),ease=fraction*fraction*(3-2*fraction);
   g={x:g.x+(target.x-g.x)*ease,y:g.y+(target.y-g.y)*ease,scale:g.scale+(target.scale-g.scale)*ease};active=frame.area?.id||null;
  }
  geometry=g;focus=active;plane.style.transform=`translate(${g.x}px,${g.y}px) scale(${g.scale})`;
  // Opacity and focus share the lesson clock: pause/seek never leave a CSS animation running.
  const mix=outgoing&&!motion?.matches?Math.max(0,Math.min(1,local/fadeMs)):1;
  plane.style.opacity=String(ready?mix:0);if(outgoing){outgoing.style.opacity='1';if(mix===1)clearOutgoing();}
  $('.lesson-focus').textContent=active?'Vacib sahə avtomatik göstərilir.':'Ümumi görünüş';
 }
 function tick(){clear();if(!playing||!ready||dead)return;
  timer=clock.set(()=>{timer=null;position=time();started=clock.now();
   if(position>=total){playing=false;cancelAudio();render();status();return;}
   if(position>=offsets[index+1]){loadSlide(index+1,true);render();status();return;}
   render();tick();
  },Math.min(16,(offsets[index+1]-time())/rate));
 }
 function pause(){position=time();started=clock.now();playing=false;cancelAudio();clear();render();status();}
 function seek(value){const n=Number(value);if(!Number.isFinite(n))return;clear();clearOutgoing();position=Math.max(0,Math.min(total,n));started=clock.now();
  let next=0;while(next<lesson.slides.length-1&&position>=offsets[next+1])next++;
  if(next!==index)loadSlide(next);else syncAudio();if(position===total)playing=false;render();status();tick();
 }
 function play(){if(dead)return;if(playing){pause();return;}if(!ready)return;if(position>=total)seek(0);playing=true;started=clock.now();syncAudio();status();tick();}
 function go(next){pause();seek(offsets[Math.max(0,Math.min(next,lesson.slides.length-1))]);}
 function speed(value){const n=Number(value);if(!rates.includes(n))return;position=time();started=clock.now();rate=n;chosenRate=true;if(audio)audio.playbackRate=rate;$('[data-speed]').value=n;render();tick();}
 const manual=e=>{if(e.target.closest?.('[data-play],[data-speed],[data-sound]'))return;pause();};
 root.addEventListener('wheel',manual,{passive:true});root.addEventListener('pointerdown',manual);
 $('[data-play]').onclick=play;
 $('[data-sound]').onclick=()=>{const local=time()-offsets[index],wasPlaying=playing;pause();$('.lesson-status').classList.remove('lesson-error');voice=!voice;lesson=voice?voicedLesson:silentLesson;timing();seekbar.max=total;$('[data-total]').textContent=format(total);if(!chosenRate)rate=voice?1:1.75;seek(offsets[index]+Math.min(local,lesson.slides[index].playback.duration_ms-1));if(wasPlaying)play();status();};
 seekbar.oninput=()=>{const value=seekbar.value;pause();seek(value);};$('[data-speed]').onchange=e=>speed(e.target.value);
 const keys=e=>{if(e.target.matches('input,textarea,select,button'))return;
  if(['ArrowRight','ArrowLeft','Home','End',' ','k'].includes(e.key)){e.preventDefault();
   if(e.key===' '||e.key==='k')play();else{pause();seek(e.key==='Home'?0:e.key==='End'?total:position+(e.key==='ArrowRight'?5000:-5000));}}
 };root.addEventListener('keydown',keys);
 const hidden=()=>{if(doc.hidden)pause();};doc.addEventListener('visibilitychange',hidden);
 const resize=()=>render();win.addEventListener('resize',resize);motion?.addEventListener?.('change',resize);
 const observer=win.ResizeObserver?new win.ResizeObserver(resize):null;observer?.observe(stage);
 seek(0);
 return {play,pause,go,seek,speed,snapshot:()=>({index,playing,elapsed:time()-offsets[index],position:time(),total,rate,voice,focus,ready,geometry}),destroy(){pause();dead=true;clearOutgoing();for(const a of audioCache.values()){a.pause();a.onended=a.onerror=a.onwaiting=a.onplaying=null;a.removeAttribute?.('src');}img.onload=img.onerror=null;observer?.disconnect();win.removeEventListener('resize',resize);motion?.removeEventListener?.('change',resize);doc.removeEventListener('visibilitychange',hidden);root.removeEventListener('wheel',manual);root.removeEventListener('pointerdown',manual);root.removeEventListener('keydown',keys);root.replaceChildren();}};
}

global.BeinLesson={mount,validate,transform};if(typeof module!=='undefined')module.exports=global.BeinLesson;
})(typeof window==='undefined'?globalThis:window);
