/* Bounded 16 kHz PCM worklets. No audio persistence or network access. */
class BeinCallCapture extends AudioWorkletProcessor{
  constructor(){
    super();this.pending=0;this.bounded=false;this.diagnostics=false;this.dropped=0;this.elapsed=0;
    this.port.onmessage=e=>{if(e.data==='bounded')this.bounded=true;else if(e.data==='consumed'&&this.pending>0)this.pending--;else if(e.data==='diagnostics')this.diagnostics=true;};
  }
  process(inputs){
    const data=inputs[0]?.[0];
    // The audio thread keeps running during main-thread stalls (including native
    // dialogs). Never queue seconds of old speech and flush them as a network
    // burst on resume. At most 32 render quanta (~256 ms) may await consumption.
    // Explicit opt-in keeps already-open pre-rollout pages compatible.
    if(data?.length&&this.bounded&&this.pending>=32)this.dropped+=data.length;
    if(data?.length&&(!this.bounded||this.pending<32)){if(this.bounded)this.pending++;this.port.postMessage(data.slice());}
    this.elapsed+=data?.length||128;
    if(this.diagnostics&&this.elapsed>=16000){this.elapsed=0;this.port.postMessage({diagnostics:true,capture_dropped_samples:this.dropped});}
    return true;
  }
}
class BeinCallPlayback extends AudioWorkletProcessor{
  constructor(){super();this.ring=new Float32Array(16000);this.read=0;this.write=0;this.available=0;this.diagnostics=false;this.started=false;this.underrun=0;this.overflow=0;this.elapsed=0;
    this.port.onmessage=e=>{if(e.data==='diagnostics'){this.diagnostics=true;return;}if(!(e.data instanceof Float32Array)||e.data.length>3200)return;this.started=true;for(const value of e.data){this.ring[this.write]=value;this.write=(this.write+1)%16000;if(this.available<16000)this.available++;else{this.read=(this.read+1)%16000;this.overflow++;}}};
  }
  process(_,outputs){const out=outputs[0]?.[0];if(out)for(let i=0;i<out.length;i++){if(this.available){out[i]=this.ring[this.read];this.read=(this.read+1)%16000;this.available--;}else{out[i]=0;if(this.started)this.underrun++;}}this.elapsed+=out?.length||128;if(this.diagnostics&&this.elapsed>=16000){this.elapsed=0;this.port.postMessage({diagnostics:true,playback_underrun_samples:this.underrun,playback_overflow_samples:this.overflow});}return true;}
}
registerProcessor('bein-call-capture',BeinCallCapture);registerProcessor('bein-call-playback',BeinCallPlayback);
