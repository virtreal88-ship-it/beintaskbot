const frame=document.querySelector('iframe'),out=document.querySelector('output');
const wait=ms=>new Promise(r=>setTimeout(r,ms)),passed=[];
const check=(ok,name)=>{if(!ok)throw Error(name);passed.push(name);out.textContent=passed.join('\n');};
async function until(fn,ms=15000){const end=performance.now()+ms;while(!fn()){if(performance.now()>end)throw Error('Timeout');await wait(50);}}
document.querySelector('button').onclick=async()=>{try{
 const w=frame.contentWindow,d=frame.contentDocument,$=s=>d.querySelector(s),play=$('[data-play]'),seek=$('[data-seek]'),speed=$('[data-speed]');
 const audio=()=>d.querySelector('audio'),pos=()=>Number(seek.value);
 check(speed.value==='1','Narrated lesson defaults to 1x');play.click();await until(()=>pos()>1800);
 check(true,'Real speech clock advances');play.click();const t=pos(),transform=$('.lesson-plane').style.transform;await wait(400);check(pos()===t&&transform===$('.lesson-plane').style.transform,'Pause freezes speech clock and focus');
 play.click();await until(()=>pos()>t+300);check(true,'Resume advances');
 const before=pos();speed.value='1.75';speed.dispatchEvent(new w.Event('change'));check(Math.abs(pos()-before)<100,'Speed preserves position');
 seek.value=26000;seek.dispatchEvent(new w.Event('input'));await until(()=>!play.disabled);check(pos()===26000,'Audio/image seek exact');play.click();await until(()=>pos()>26300);play.click();
 seek.value=0;seek.dispatchEvent(new w.Event('input'));await until(()=>!play.disabled);speed.value='1';speed.dispatchEvent(new w.Event('change'));play.click();
 const steps=new Set(),moving=new Set();let previous=-1;let last=-1;
 await until(()=>{const step=$('.lesson-count').textContent;steps.add(step);const p=pos();if(p>previous)moving.add(step);previous=p;last=Math.max(last,p);return p===Number(seek.max)&&play.getAttribute('aria-pressed')==='false';},120000);
 check(steps.size===8&&moving.size===8,'All eight narration clips advance in full 1x playback');check(play.getAttribute('aria-pressed')==='false','Narrated lesson completes');
 $('[data-sound]').click();check(speed.value==='1','Explicit chosen rate persists on sound toggle');check($('[data-sound]').getAttribute('aria-pressed')==='false','Silent viewing available');
 out.textContent='PASS\n'+passed.join('\n');
}catch(e){out.textContent='FAIL '+e.message+'\n'+passed.join('\n');}};
