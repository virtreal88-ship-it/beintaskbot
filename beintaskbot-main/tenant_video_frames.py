"""Shared bounded video decoding for customer replies and knowledge review."""
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_VIDEO_SECONDS = 120
MAX_VIDEO_AUDIO_BYTES = 4 * 1024 * 1024


def validate_video_duration(seconds):
    if not isinstance(seconds, (int, float)) or not 0 < seconds <= MAX_VIDEO_SECONDS:
        raise ValueError('video_duration_limit')


def sample_frames(data, mime, *, max_frames=2):
    """Return bounded, timestamped frames from a local MP4/WebM byte string.

    The decoder cannot open secondary URLs or playlists. Sampling positions are
    deterministic so identical clips produce identical frame hashes.
    """
    if mime not in {'video/mp4', 'video/webm'}:
        raise ValueError('video_unsupported_format')
    if not isinstance(max_frames, int) or not 1 <= max_frames <= 4:
        raise ValueError('video_frame_limit')
    if not isinstance(data, (bytes, bytearray)) or not 0 < len(data) <= MAX_VIDEO_BYTES:
        raise ValueError('video_size_limit')
    if not shutil.which('ffmpeg'):
        raise LookupError('video_decoder_unavailable')
    with tempfile.TemporaryDirectory(prefix='bounded-video-') as directory:
        source = Path(directory) / ('clip.mp4' if mime == 'video/mp4' else 'clip.webm')
        source.write_bytes(data)
        common = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov,matroska,webm']
        probe = subprocess.run(
            ['ffmpeg', '-nostdin', '-v', 'error', *common, '-i', str(source),
             '-map', '0:v:0', '-an', '-sn', '-dn', '-t', str(MAX_VIDEO_SECONDS + 1),
             '-threads', '1', '-progress', 'pipe:1', '-f', 'null', '-'],
            capture_output=True, timeout=12)
        ticks = [int(line.split('=')[1]) for line in probe.stdout.decode().splitlines()
                 if line.startswith('out_time_us=') and line.split('=')[1].lstrip('-').isdigit()]
        duration = max(ticks, default=0) / 1_000_000
        if probe.returncode:
            raise ValueError('video_duration_limit')
        validate_video_duration(duration)
        positions = sorted({round(duration * index / max_frames, 3)
                            for index in range(max_frames)})
        frames = []
        for index, second in enumerate(positions):
            output = Path(directory) / f'frame{index}.jpg'
            result = subprocess.run(
                ['ffmpeg', '-nostdin', '-v', 'error', *common, '-ss', str(second),
                 '-i', str(source), '-map', '0:v:0', '-an', '-sn', '-dn',
                 '-frames:v', '1', '-vf',
                 'scale=1280:720:force_original_aspect_ratio=decrease', '-threads', '1', str(output)],
                capture_output=True, timeout=8)
            if result.returncode or not output.exists() or output.stat().st_size > 1024 * 1024:
                raise ValueError('video_frame_failed')
            frames.append((output.read_bytes(), 'image/jpeg', {'second': second}))
        return frames


def extract_audio(data, mime):
    """Extract one bounded local mono track; no network protocols are enabled."""
    if mime not in {'video/mp4', 'video/webm'}:
        raise ValueError('video_unsupported_format')
    if not shutil.which('ffmpeg'):
        raise LookupError('video_decoder_unavailable')
    with tempfile.TemporaryDirectory(prefix='bounded-video-audio-') as directory:
        source = Path(directory) / ('clip.mp4' if mime == 'video/mp4' else 'clip.webm')
        source.write_bytes(data)
        output = Path(directory) / 'track.wav'
        result = subprocess.run(
            ['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
             '-format_whitelist', 'mov,matroska,webm', '-i', str(source), '-map', '0:a:0',
             '-vn', '-sn', '-dn', '-t', str(MAX_VIDEO_SECONDS + 1), '-ac', '1', '-ar', '16000',
             '-threads', '1', str(output)], capture_output=True, timeout=12)
        if result.returncode or not output.exists() or not 0 < output.stat().st_size < MAX_VIDEO_AUDIO_BYTES:
            raise LookupError('video_audio_unavailable')
        from tenant_connect_voice import validate_audio
        return validate_audio(output.read_bytes(), 'wav', 'audio/wav', max_seconds=MAX_VIDEO_SECONDS)
