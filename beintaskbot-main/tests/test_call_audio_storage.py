"""Real local encoder/decoder on synthetic tones; no user audio or providers."""
import io
import math
import shutil
import struct
import subprocess
import wave
import pytest
from tenant_call_audio_storage import compress
from tenant_linear_policy import TenantLinearError


def test_opus_keeps_both_tracks_and_bounded_size():
    if not shutil.which('ffmpeg'):pytest.fail('ffmpeg is required for managed recordings')
    frames=[(int(12000*math.sin(2*math.pi*440*i/16000)),int(9000*math.sin(2*math.pi*880*i/16000))) for i in range(32000)]
    pcm=b''.join(struct.pack('<hh',*frame) for frame in frames);target=io.BytesIO()
    with wave.open(target,'wb') as writer:
        writer.setnchannels(2);writer.setsampwidth(2);writer.setframerate(16000);writer.writeframes(pcm)
    encoded=compress(target.getvalue())
    assert encoded.startswith(b'OggS') and len(encoded)<len(pcm)//3
    decoded=subprocess.run([shutil.which('ffmpeg'),'-v','quiet','-nostdin','-f','ogg','-i','pipe:0',
        '-f','s16le','-acodec','pcm_s16le','-ac','2','-ar','16000','pipe:1'],input=encoded,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,check=True).stdout
    output=list(struct.iter_unpack('<hh',decoded));assert len(output)==len(frames)
    for channel in (0,1):
        dot=sum(a[channel]*b[channel] for a,b in zip(frames,output))
        scale=math.sqrt(sum(a[channel]**2 for a in frames)*sum(b[channel]**2 for b in output))
        assert dot/scale>.9


def test_encoder_missing_fails_without_lying_about_saved_recording(monkeypatch):
    monkeypatch.setattr(shutil,'which',lambda _:None)
    with pytest.raises(TenantLinearError,match='recording_encoder_unavailable'):compress(b'unused')
