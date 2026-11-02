"""Local synthetic audio only: no provider, database or customer traffic."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tenant_connect_voice import VoiceError
from tenant_customer_voice import MAX_RECORDING_BYTES, validate_recording


class CustomerVoiceTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
    def test_synthetic_browser_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            for mime, codec, container in [('audio/ogg;codecs=opus', 'libopus', 'ogg'),
                                            ('audio/webm;codecs=opus', 'libopus', 'webm'),
                                            ('audio/mp4', 'aac', 'mp4')]:
                with self.subTest(mime=mime):
                    source = Path(directory) / ('tone.' + container)
                    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                                    'sine=frequency=440:duration=0.4', '-c:a', codec, str(source)],
                                   check=True, capture_output=True, timeout=20)
                    content = source.read_bytes()
                    result, _, actual_mime = validate_recording(content, mime)
                    self.assertEqual(result, content)
                    self.assertEqual(actual_mime, mime.split(';')[0])

    def test_reject_wrong_type_corrupt_payload_and_size(self):
        cases = [(b'not audio', 'audio/ogg'), (b'OggS' + b'bad', 'audio/webm'),
                 (b'RIFF', 'audio/wav'), (b'x' * (MAX_RECORDING_BYTES + 1), 'audio/ogg')]
        if shutil.which('ffmpeg'):
            cases.append((b'OggS' + b'bad', 'audio/ogg'))
        for content, mime in cases:
            with self.subTest(mime=mime), self.assertRaises(VoiceError):
                validate_recording(content, mime)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
    def test_reject_audio_exceeding_server_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'long.ogg'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                            'anullsrc=r=16000:cl=mono', '-t', '121', '-c:a', 'libopus',
                            str(source)], check=True, capture_output=True, timeout=20)
            with self.assertRaisesRegex(VoiceError, 'voice_duration_limit'):
                validate_recording(source.read_bytes(), 'audio/ogg')


if __name__ == '__main__':
    unittest.main()
