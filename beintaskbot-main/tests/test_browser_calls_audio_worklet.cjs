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
