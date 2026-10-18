const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
const data=JSON.parse(fs.readFileSync('docs/instructor-pilot/warehouse-transfer.lesson.json','utf8'));
const dom=new JSDOM('<main id="root"></main>',{url:'http://127.0.0.1/app/instructor',runScripts:'outside-only'}),w=dom.window;
w.eval(fs.readFileSync('docs/tenant-instructor-lesson.js','utf8'));
const root=w.document.getElementById('root');let now=0,id=0,tasks=new Map();
const clock={now:()=>now,set:(fn,delay)=>{tasks.set(++id,{fn,at:now+delay});return id;},clear:i=>tasks.delete(i)};
function advance(ms){const end=now+ms;while(true){const next=[...tasks.entries()].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;tasks.delete(next[0]);now=next[1].at;next[1].fn();}now=end;}
const player=w.BeinLesson.mount(root,data,{clock,resolveAsset:id=>'./'+id});
const stage=root.querySelector('.lesson-stage');stage.getBoundingClientRect=()=>({width:900,height:600});
function load(){const s=data.slides[player.snapshot().index],img=root.querySelector('img');Object.defineProperty(img,'naturalWidth',{value:s.image.width,configurable:true});Object.defineProperty(img,'naturalHeight',{value:s.image.height,configurable:true});img.onload();}
load();assert(player.snapshot().ready);player.play();advance(5000);assert.equal(player.snapshot().focus,null,'overview has no unnecessary focus');
root.dispatchEvent(new w.Event('pointerdown',{bubbles:true}));assert(!player.snapshot().playing);const time=player.snapshot().elapsed;advance(3000);assert.equal(player.snapshot().elapsed,time);player.play();advance(9000);assert.equal(player.snapshot().index,1,'resume continues remaining time');load();
stage.getBoundingClientRect=()=>({width:360,height:300});player.go(2);load();player.play();advance(1500);assert.equal(player.snapshot().focus,'area-1');assert(player.snapshot().geometry.scale<=1,'native resolution ceiling');assert(player.snapshot().geometry.scale>360/855,'mobile auto focus without clicking image');
advance(11000);assert.equal(player.snapshot().focus,null,'return to overview');
root.querySelector('[data-next]').click();load();assert.equal(player.snapshot().index,3);assert(!player.snapshot().playing,'manual next pauses');
for(let i=0;i<8;i++){player.go(i);load();assert.equal(root.querySelector('h2').textContent,data.slides[i].title);assert.equal(root.querySelectorAll('.lesson-area').length,data.slides[i].areas.length);}
player.play();advance(18000);assert(!player.snapshot().playing);assert.equal(root.querySelector('.lesson-status').textContent,'Dərs tamamlandı');
player.go(0);load();player.play();root.dispatchEvent(new w.Event('wheel'));assert(!player.snapshot().playing);
player.go(0);root.querySelector('img').onerror();assert(!player.snapshot().ready&&!player.snapshot().playing);
const t=w.BeinLesson.transform({width:855,height:656},{width:360,height:300},{x:600,y:300,width:60,height:30},50);assert(t.scale<=1&&t.x<=0&&t.y<=0);
assert.throws(()=>w.BeinLesson.mount(w.document.createElement('main'),data,{resolveAsset:()=> 'https://evil.test/image.jpg'}));
const unsafe=structuredClone(data);unsafe.slides[0].image.asset_id='../../other.jpg';assert.throws(()=>w.BeinLesson.validate(unsafe));
stage.getBoundingClientRect=()=>({width:360,height:300});player.go(0);load();player.play();advance(14000);assert.equal(player.snapshot().index,1);root.dispatchEvent(new w.Event('pointerdown'));load();assert(!player.snapshot().playing,'manual pause while next image loads prevents deferred auto-resume');player.go(0);load();player.play();advance(1500);assert.equal(player.snapshot().focus,'area-1','small-screen navigation is automatically enlarged');player.destroy();assert.equal(tasks.size,0);assert.equal(root.children.length,0);dom.window.close();console.log('PASS: 8 slides, autoplay, focus/native limit, overview, pause/resume, manual controls, image failure and origin guard');
