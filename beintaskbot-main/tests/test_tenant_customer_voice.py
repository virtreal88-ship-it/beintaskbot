"""Local synthetic audio only: no provider, database or customer traffic."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tenant_connect_voice import VoiceError
from tenant_customer_voice import MAX_RECORDING_BYTES, validate_recording, native_opus


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
                    opus=native_opus(content,mime)
                    self.assertTrue(opus.startswith(b'OggS'))
                    self.assertIn(b'OpusHead',opus[:200])
                    validate_recording(opus,'audio/ogg')

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
    def test_native_opus_rebuilds_browser_timestamps_from_audio_samples(self):
        source = subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000:duration=3.2',
            # Model a browser timestamp jump with no matching audio samples.
            '-af', r'asetpts=PTS+if(gte(T\,1.2)\,0.08/TB\,0)',
            '-c:a', 'libopus', '-f', 'webm', 'pipe:1',
        ], check=True, capture_output=True, timeout=20).stdout
        opus = native_opus(source, 'audio/webm')
        offset = page = encoded_samples = 0
        while offset < len(opus):
            self.assertEqual(opus[offset:offset + 4], b'OggS')
            data = offset + 27 + opus[offset + 26]
            laces = opus[offset + 27:data]
            granule = int.from_bytes(opus[offset + 6:offset + 14], 'little')
            if page == 0:
                pre_skip = int.from_bytes(opus[data + 10:data + 12], 'little')
            if page >= 2:
                # Each completed packet from libopus's default 20 ms frames
                # accounts for 960 samples. Only the final page can trim it.
                encoded_samples += 960 * sum(lace < 255 for lace in laces)
                if opus[offset + 5] & 4:
                    self.assertLessEqual(granule, encoded_samples)
                    self.assertAlmostEqual((granule - pre_skip) / 48000, 3.2, places=2)
                else:
                    self.assertEqual(granule, encoded_samples, f'Ogg page {page}')
            offset = data + sum(laces)
            page += 1
        self.assertGreaterEqual(page, 5)
        self.assertEqual(offset, len(opus))

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
