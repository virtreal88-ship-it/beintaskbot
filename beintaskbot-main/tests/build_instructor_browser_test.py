"""Build a real-browser regression page beside the offline lesson (HTTP only).
Run: python tests/build_instructor_browser_test.py, then open
http://127.0.0.1:8888/player-browser-test.html in the browser.
No fake clock; exercises the built HTML, real image decode and DOM controls.
"""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
script=r'''
const result=document.querySelector('output'), frame=document.querySelector('iframe');
const checks=[];let errors=[];
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const check=(ok,name)=>{if(!ok)throw Error(name);checks.push(name);result.textContent=checks.join('\n');};
async function until(fn,timeout=12000){const end=performance.now()+timeout;while(!fn()){if(performance.now()>end)throw Error('Timeout');await wait(50);}}
frame.onload=async()=>{try{
 const d=frame.contentDocument,w=frame.contentWindow,$=s=>d.querySelector(s);
 w.addEventListener('error',e=>errors.push(e.message));
 await until(()=>$('[data-play]')&&!$('[data-play]').disabled);
 const button=$('[data-play]'),slider=$('[data-seek]'),speed=$('[data-speed]');
 const pos=()=>Number(slider.value),shape=()=>$('.lesson-plane').style.transform;
 const seek=value=>{slider.value=value;slider.dispatchEvent(new w.Event('input',{bubbles:true}));};
 check($('img').naturalWidth===855,'Real screenshot decoded');check(!$('audio')&&!$('[data-sound]'),'No audio or sound control');
 check(speed.value==='1.25','Default playback rate is 1.25');
 const initial=shape();button.click();await until(()=>pos()>2400);
 check(pos()>2400,'Play advances actual DOM time');check(shape()!==initial,'Autofocus changes actual image transform');
 await until(()=>$('.lesson-count').textContent.includes('2 / 8'));
 check(pos()>=12000,'Real timer automatically advances to step 2');
 await until(()=>!button.disabled&&Number($('.lesson-plane').style.opacity)>0&&Number($('.lesson-plane').style.opacity)<1);
 check(!!$('.lesson-outgoing'),'Real two-image crossfade');button.click();const subtitle=$('[data-subtitle]').textContent;const stopped=pos(),frozen=shape(),alpha=$('.lesson-plane').style.opacity;await wait(400);
 check($('.lesson-plane').style.opacity===alpha,'Pause freezes crossfade opacity');
 check(pos()===stopped&&shape()===frozen&&$('[data-subtitle]').textContent===subtitle,'Pause freezes time, focus and subtitles');
 button.click();await until(()=>pos()>stopped+300);check(true,'Resume advances from paused position');
 const beforeRate=pos();speed.value='1.5';speed.dispatchEvent(new w.Event('change',{bubbles:true}));check(Math.abs(pos()-beforeRate)<80,'Live speed change preserves position');
 seek(50100);await until(()=>!button.disabled);
 check($('.lesson-count').textContent.includes('5 / 8')&&pos()===50100,'Seek restores step and exact position');check(!$('.lesson-outgoing')&&$('.lesson-plane').style.opacity==='1','Seek cuts immediately without a transition');check($('[data-subtitle]').textContent.includes('Beta TEST'),'Seek selects matching subtitle');
 seek(107600);await until(()=>!button.disabled);button.click();await until(()=>pos()===108000);
 check(button.getAttribute('aria-pressed')==='false','Playback reaches total and stops');
 check($('.lesson-count').textContent.includes('8 / 8'),'Completion uses step 8');
 check(errors.length===0,'No JavaScript runtime errors');
 seek(0);await until(()=>!button.disabled);speed.value='1.75';speed.dispatchEvent(new w.Event('change',{bubbles:true}));button.click();
 let blank=false,overscale=false,overlap=false,clipped=false;const steps=new Set(),captions=new Set();
 await until(()=>{steps.add($('.lesson-count').textContent);captions.add($('[data-subtitle]').textContent);const cap=$('.lesson-subtitles').getBoundingClientRect(),stage=$('.lesson-stage').getBoundingClientRect(),p=$('[data-subtitle]');if(p.scrollHeight>p.clientHeight+1)clipped=true;for(const node of d.querySelectorAll('.lesson-plane .lesson-area')){const r=node.getBoundingClientRect();if(r.right>stage.left&&r.left<stage.right&&r.bottom>stage.top&&r.top<stage.bottom&&cap.left<r.right&&cap.right>r.left&&cap.top<r.bottom&&cap.bottom>r.top)overlap=true;}const image=$('img');if(!image.complete||!image.naturalWidth){if(!$('.lesson-outgoing'))blank=true;}const matrix=new w.DOMMatrix(getComputedStyle($('.lesson-plane')).transform);if(matrix.a>1.00001)overscale=true;return pos()===108000&&button.getAttribute('aria-pressed')==='false';},75000);
 check(steps.size===8&&captions.size>=20,'Full 1.75 playback visits eight steps and timed captions');check(!blank&&!overscale,'No blank image frames or scaling beyond native resolution');check(!overlap&&!clipped,'Captions avoid highlighted fields and fit two lines');
 result.textContent='PASS\n'+checks.join('\n');
 }catch(e){result.textContent='FAIL: '+e.message+'\n'+checks.join('\n');}};
frame.src='beinsystems-warehouse-transfer-az.html';
'''
html='<!doctype html><html lang="en"><meta charset="utf-8"><title>Instructor real-browser regression</title><h1>Real-clock browser regression</h1><output aria-live="polite" style="white-space:pre-wrap">Running…</output><br><iframe title="Built lesson under test" width="360" height="700"></iframe><script>'+script+'</script></html>'
(root/'output/lessons/player-browser-test.html').write_text(html,encoding='utf-8')
print('Built real-clock browser regression page')
