/* Bounded 16 kHz PCM worklets. No audio persistence or network access. */
class BeinCallCapture extends AudioWorkletProcessor{
  process(inputs){const data=inputs[0]?.[0];if(data?.length)this.port.postMessage(data.slice());return true;}
}
class BeinCallPlayback extends AudioWorkletProcessor{
  constructor(){super();this.ring=new Float32Array(16000);this.read=0;this.write=0;this.available=0;
    this.port.onmessage=e=>{if(!(e.data instanceof Float32Array)||e.data.length>3200)return;for(const value of e.data){this.ring[this.write]=value;this.write=(this.write+1)%16000;if(this.available<16000)this.available++;else this.read=(this.read+1)%16000;}};
  }
  process(_,outputs){const out=outputs[0]?.[0];if(out)for(let i=0;i<out.length;i++){if(this.available){out[i]=this.ring[this.read];this.read=(this.read+1)%16000;this.available--;}else out[i]=0;}return true;}
}
registerProcessor('bein-call-capture',BeinCallCapture);registerProcessor('bein-call-playback',BeinCallPlayback);
