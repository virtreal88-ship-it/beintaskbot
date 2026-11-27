const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('docs/browser-call-audio.js','utf8');
function fixture(){
  let Capture;const queued=[];
  vm.runInNewContext(source,{Float32Array,AudioWorkletProcessor:class{constructor(){this.port={postMessage:x=>queued.push(x)};}},registerProcessor:(name,C)=>{if(name==='bein-call-capture')Capture=C;}});
  const capture=new Capture();return {capture,queued,frame:value=>capture.process([[new Float32Array(128).fill(value)]])};
}
const f=fixture();f.capture.port.onmessage({data:'bounded'});
for(let i=0;i<400;i++)f.frame(i);
assert.equal(f.queued.length,32,'a blocked document has a bounded audio queue');
assert(f.queued.reduce((n,x)=>n+x.length*2,0)<65536,'resuming cannot flush seconds of PCM');
for(let i=0;i<32;i++)f.capture.port.onmessage({data:'consumed'});
f.frame(999);assert.equal(f.queued.at(-1)[0],999,'resume captures current speech rather than replaying discarded audio');
for(let i=0;i<100;i++)f.capture.port.onmessage({data:'consumed'});
assert.equal(f.capture.pending,0,'duplicate acknowledgments cannot create extra credits');
const legacy=fixture();for(let i=0;i<40;i++)legacy.frame(i);assert.equal(legacy.queued.length,40,'already-open old clients remain compatible until reload');
console.log('PASS: bounded capture, stall recovery without stale replay, duplicate acknowledgments and rollout compatibility');


const diagnostic=fixture();diagnostic.capture.port.onmessage({data:'bounded'});
diagnostic.capture.port.onmessage({data:'diagnostics'});
for(let i=0;i<125;i++)diagnostic.frame(1);
const stats=diagnostic.queued.filter(item=>item.diagnostics);
assert.equal(stats.length,1);
assert.equal(stats[0].capture_dropped_samples,(125-32)*128);
assert.equal(diagnostic.queued.filter(item=>item instanceof Float32Array).length,32);
console.log('PASS capture drop counters require explicit opt-in');


let Playback;const reports=[];
vm.runInNewContext(source,{Float32Array,AudioWorkletProcessor:class{constructor(){this.port={postMessage:x=>reports.push(x)};}},registerProcessor:(name,C)=>{if(name==='bein-call-playback')Playback=C;}});
const playback=new Playback(),output=new Float32Array(128);
for(let i=0;i<125;i++)playback.process([],[[output]]);
assert.equal(playback.underrun,0,'waiting for first audio must not count as a playback gap');
playback.port.onmessage({data:'diagnostics'});
playback.port.onmessage({data:new Float32Array(128)});
for(let i=0;i<126;i++)playback.process([],[[output]]);
assert.equal(reports.at(-1).playback_underrun_samples,125*128);
for(let i=0;i<6;i++)playback.port.onmessage({data:new Float32Array(3200)});
assert.equal(playback.overflow,3200);
console.log('PASS playback underrun and overflow counters after first audio');
