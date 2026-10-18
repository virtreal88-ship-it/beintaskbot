const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
const data=JSON.parse(fs.readFileSync('docs/instructor-pilot/warehouse-transfer.lesson.json','utf8'));
const dom=new JSDOM('<main id="root"></main>',{url:'http://127.0.0.1/app/instructor',runScripts:'outside-only'}),w=dom.window;
let reduced=false;w.matchMedia=()=>({get matches(){return reduced;}});
w.eval(fs.readFileSync('docs/tenant-instructor-lesson.js','utf8'));
const root=w.document.getElementById('root');let now=0,id=0,tasks=new Map();
const clock={now:()=>now,set:(fn,delay)=>{tasks.set(++id,{fn,at:now+delay});return id;},clear:i=>tasks.delete(i)};
function advance(ms){const end=now+ms;let count=0;while(true){const next=[...tasks.entries()].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;assert(++count<100000,'timer loop');tasks.delete(next[0]);now=next[1].at;next[1].fn();}now=end;}
const subtitles=JSON.parse(fs.readFileSync('docs/instructor-pilot/warehouse-transfer.subtitles.json','utf8'));
const player=w.BeinLesson.mount(root,data,{clock,subtitles,resolveAsset:id=>'./'+id});
const stage=root.querySelector('.lesson-stage');let width=360;stage.getBoundingClientRect=()=>({width,height:width===900?600:300});
function load(){const s=data.slides[player.snapshot().index],img=root.querySelector('img');Object.defineProperty(img,'naturalWidth',{value:s.image.width,configurable:true});Object.defineProperty(img,'naturalHeight',{value:s.image.height,configurable:true});img.onload();}
load();assert.equal(root.querySelector('[data-subtitle]').textContent,subtitles.slides[0][0].text);assert.equal(root.querySelector('[data-sound]'),null);assert.equal(root.querySelector('audio'),null);assert.equal(player.snapshot().rate,1.25);assert.equal(player.snapshot().total,108000);
// Each permitted speed advances the same lesson clock; changing speed preserves position.
for(const rate of [.75,1,1.25,1.5,1.75,2]){player.seek(0);player.speed(rate);player.play();advance(500);assert.equal(player.snapshot().position,500*rate);player.pause();}
player.speed(1);player.seek(5000);assert.equal(root.querySelector('[data-subtitle]').textContent,subtitles.slides[0][1].text);player.pause();const caption=root.querySelector('[data-subtitle]').textContent;advance(500);assert.equal(root.querySelector('[data-subtitle]').textContent,caption);player.seek(1725);const halfway=player.snapshot().geometry;assert(halfway.scale>360/855&&halfway.scale<1);
player.play();advance(80);player.pause();const frozen=player.snapshot(),style=root.querySelector('.lesson-plane').getAttribute('style');advance(3000);assert.deepEqual(player.snapshot(),frozen);assert.equal(root.querySelector('.lesson-plane').getAttribute('style'),style);
player.play();advance(80);player.pause();assert.equal(player.snapshot().position,frozen.position+80);
player.seek(1725);assert.deepEqual(player.snapshot().geometry,halfway,'seek reconstructs interpolated focus');
// Actual range event, backward/forward across all eight images, and manual controls.
const slider=root.querySelector('[data-seek]');slider.value=25000;slider.dispatchEvent(new w.Event('input'));load();assert.equal(player.snapshot().position,25000);assert.equal(player.snapshot().index,2);
for(let i=0,offset=0;i<8;i++){player.seek(offset+2000);load();assert.equal(player.snapshot().index,i);assert.equal(root.querySelector('h2').textContent,data.slides[i].title);assert.equal(root.querySelector('[data-subtitle]').textContent,subtitles.slides[i].filter(c=>c.at_ms<=2000).at(-1).text);assert(player.snapshot().geometry.scale<=1);offset+=data.slides[i].playback.duration_ms;}
assert.equal(root.querySelectorAll('nav button').length,1,'one compact play control');assert.equal(root.querySelector('[data-back]'),null);assert.equal(root.querySelector('[data-next]'),null);
player.go(6);load();assert.equal(player.snapshot().position,76000);assert(!player.snapshot().playing);
assert.equal(root.querySelector('[data-play]').getAttribute('aria-label'),'Davam et');root.querySelector('[data-play]').click();assert.equal(root.querySelector('[data-play]').getAttribute('aria-label'),'Pauza');player.pause();
player.seek(11990);load();player.play();advance(20);assert.equal(player.snapshot().index,1);assert(!root.querySelector('[data-play]').disabled,'Pause remains available while next image loads');root.querySelector('[data-play]').click();load();assert(!player.snapshot().playing,'pause button during loading cancels continuation');
player.seek(11990);load();player.play();advance(20);load();advance(200);player.pause();const fade=root.querySelector('.lesson-plane').style.opacity;assert(Number(fade)>0&&Number(fade)<1);advance(1000);assert.equal(root.querySelector('.lesson-plane').style.opacity,fade);assert(root.querySelector('.lesson-outgoing'));player.play();advance(700);player.pause();assert.equal(root.querySelector('.lesson-outgoing'),null);
player.seek(108000);load();assert(!player.snapshot().playing);assert.equal(root.querySelector('[data-current]').textContent,'1:48');player.play();load();assert.equal(player.snapshot().index,0);player.pause();
width=900;player.seek(2000);load();assert.equal(player.snapshot().focus,null,'desktop overview avoids needless zoom');
width=360;reduced=true;player.seek(1401);assert.equal(player.snapshot().geometry.scale,1,'reduced motion has no interpolated zoom');assert.equal(root.querySelector('.lesson-plane').style.opacity,'1');assert.equal(root.querySelector('.lesson-subtitles').style.opacity,'1','reduced motion disables caption fade');reduced=false;
player.seek(0);root.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));assert.equal(player.snapshot().position,5000);root.dispatchEvent(new w.KeyboardEvent('keydown',{key:' ',bubbles:true}));assert(player.snapshot().playing);root.dispatchEvent(new w.Event('wheel'));assert(!player.snapshot().playing);
player.seek(1000);player.play();advance(100);player.speed(2);assert.equal(player.snapshot().position,1100);advance(100);player.pause();assert.equal(player.snapshot().position,1300,'speed change preserves clock');
player.seek(107900);load();player.play();advance(100);assert.equal(player.snapshot().position,108000);assert(!player.snapshot().playing,'automatic end retains total time');
player.go(0);root.querySelector('img').onerror();assert(!player.snapshot().ready&&!player.snapshot().playing);
assert.throws(()=>w.BeinLesson.mount(w.document.createElement('main'),data,{resolveAsset:()=> 'https://evil.test/image.jpg'}));
const unsafe=structuredClone(data);unsafe.slides[0].image.asset_id='../../other.jpg';assert.throws(()=>w.BeinLesson.validate(unsafe));
const badCaptions=JSON.parse(JSON.stringify(subtitles));badCaptions.slides[0][0].at_ms=-1;assert.throws(()=>w.BeinLesson.mount(w.document.createElement('main'),data,{subtitles:badCaptions}));
const place=w.BeinLesson.captionPosition({width:320,height:300},{width:200,height:42},[{x:50,y:230,width:220,height:60}]);assert(place.y+42<=220,'caption moves above highlighted bottom field');
const fallback=w.BeinLesson.captionPosition({width:320,height:300},{width:200,height:42},[{x:0,y:0,width:320,height:300}]);assert(fallback.outside&&fallback.y>300,'no free space places caption below image');
player.seek(5100);player.play();advance(30);player.pause();const opacity=root.querySelector('.lesson-subtitles').style.opacity;advance(1000);assert.equal(root.querySelector('.lesson-subtitles').style.opacity,opacity,'pause freezes caption fade');player.seek(5000);assert.equal(root.querySelector('.lesson-subtitles').style.opacity,'1','seek immediately shows caption');
player.destroy();assert.equal(tasks.size,0);assert.equal(root.children.length,0);dom.window.close();console.log('PASS: timeline, 6 speeds/crossfade, seek/focus reconstruction, frozen animation/resume, 8 slides, loading pause, replay, mobile/native limit, reduced motion, keyboard, image/origin guards');
