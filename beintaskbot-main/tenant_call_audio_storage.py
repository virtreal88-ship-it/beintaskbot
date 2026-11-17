"""Bounded, stereo recording compression after signature/PCM validation."""
import shutil
import subprocess
from tenant_linear_policy import TenantLinearError


def compress(wav):
    executable=shutil.which('ffmpeg')
    if not executable:raise TenantLinearError('recording_encoder_unavailable',503)
    # No URLs, filenames, user arguments or temporary plaintext files. Both
    # channels stay in their original employee/caller order. Provider WAV is
    # already constrained to 16 kHz, 16-bit PCM by the signed bundle decoder.
    try:
        result=subprocess.run([executable,'-hide_banner','-loglevel','quiet','-nostdin',
            '-protocol_whitelist','pipe','-f','wav','-i','pipe:0','-map','0:a:0',
            '-c:a','libopus','-b:a','48k','-vbr','constrained','-application','voip',
            '-ac','2','-ar','48000','-f','ogg','pipe:1'],input=wav,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,timeout=60,check=True)
        encoded=result.stdout
        if not encoded.startswith(b'OggS') or len(encoded)>8*1024*1024:
            raise ValueError()
        return encoded
    except (OSError,ValueError,subprocess.SubprocessError):
        raise TenantLinearError('recording_encoding_failed',503) from None
