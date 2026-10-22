"""Mock-only paid APIs; real synthetic OGG decoding when ffmpeg is installed."""
import asyncio
import io
import shutil
import sys
import types
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

if 'psycopg' not in sys.modules:
    pg = types.ModuleType('psycopg')
    pg.connect = MagicMock()
    rows_module = types.ModuleType('psycopg.rows')
    rows_module.dict_row = object()
    sys.modules['psycopg'] = pg
    sys.modules['psycopg.rows'] = rows_module

import tenant_ai_agent_worker as worker
import tenant_ai_agent_connect as adapter
import tenant_ai_agent_run_store as runs
import tenant_connect_voice as voice
import tenant_connect_transport as transport
from tenant_chat_ai_provider import ChatAIProviderError, transcribe
from tenant_linear_policy import TenantLinearError

TENANT = '00000000-0000-0000-0000-000000000001'
CHANNEL = '00000000-0000-0000-0000-000000000002'
MESSAGE = '00000000-0000-0000-0000-000000000003'
RUN = '00000000-0000-0000-0000-000000000004'
JOB = dict(tenant_id=TENANT, channel_id=CHANNEL, lead_id=12, provider='connect',
           conversation_id='994501234567@s.whatsapp.net', agent_id=CHANNEL,
           lease_token=RUN, revision=1, event_fingerprint='event',
           payload={'message_id': MESSAGE})
RESERVATION = dict(run_id=RUN, api_key='central-key', credential_source='platform',
                   model='model', config={}, agent_language='az', agent_style='short',
                   conversation_revision=1, mode='automatic', auto_send_enabled=True)
CONFIG = dict(scopes=[{'provider': 'connect', 'channel_id': CHANNEL}], agent_model='model',
              chat_model='model')
AUDIO = (Path(__file__).parent / 'fixtures/connect-voice-tone.ogg').read_bytes()


def row(mid=MESSAGE, kind='audio', text='', at='2026-10-16T01:00:00Z'):
    return dict(external_id=mid, direction='incoming', body=text, message_type=kind,
                media_url='', happened_at=at, transcript='')


class WorkerVoice(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.rows = [row()]
        self.claims = {}
        self.reserved = set()
        self.active = True
        def reserve(job, **kwargs):
            key = (job['tenant_id'], job['payload']['message_id'])
            if key in self.reserved:
                raise TenantLinearError('Already reserved', 409)
            self.reserved.add(key)
            return dict(RESERVATION)
        def claim(job, reservation, source, model):
            key = (job['tenant_id'], job['channel_id'], source['external_id'])
            if key in self.claims:
                text = self.claims[key]
                if text: return text
                raise voice.VoiceError('voice_already_claimed')
            self.claims[key] = ''
        def finish(job, reservation, source, *, text='', code=''):
            key = (job['tenant_id'], job['channel_id'], source['external_id'])
            self.claims[key] = text
            if text:
                source['body'] = text
                source['transcript'] = text
        def mocked(obj, name, **kwargs):
            return self.stack.enter_context(patch.object(obj, name, **kwargs))
        mocked(adapter, 'history', side_effect=lambda *_: self.rows)
        mocked(runs, 'load_agent_config', side_effect=lambda *_, **__: CONFIG if self.active else None)
        mocked(worker, 'assert_job_allowed')
        mocked(worker, '_next_open', return_value=None)
        mocked(worker, 'runtime_flag', return_value=True)
        mocked(worker, 'enabled_triggers', return_value=[])
        mocked(worker, 'knowledge_context', return_value=[])
        self.reserve = mocked(worker, 'reserve_run', side_effect=reserve)
        self.handoff = mocked(worker, 'handoff')
        self.send = mocked(transport, 'send_ai_reply_if_active', return_value='sent-id')
        self.generate = mocked(worker, 'generate', return_value=('Cavab', {'input_tokens': 5, 'output_tokens': 2, 'total_tokens': 7}))
        self.usage = mocked(worker, 'record_token_usage')
        self.audio_usage = mocked(voice, 'record_token_usage')
        self.completed = mocked(worker, 'finish_sent')
        self.failed = mocked(worker, 'finish_unknown')
        self.superseded = mocked(worker, 'finish_superseded')
        self.skip = mocked(worker, 'skip_job')
        mocked(voice, '_claim', side_effect=claim)
        self.finish_voice = mocked(voice, '_finish', side_effect=finish)
        mocked(voice, 'active', side_effect=lambda *_: self.active)
        mocked(voice, 'generation_budget_available', return_value=True)
        self.download = mocked(voice, 'download_audio', return_value=(AUDIO, 'ogg', 'audio/ogg'))
        mocked(voice, 'validate_audio', side_effect=lambda *media: media)
        self.transcribe = mocked(voice, 'transcribe', return_value=('Salam, qiyməti nə qədərdir?',
                           {'input_tokens': 20, 'output_tokens': 4, 'total_tokens': 24}))

    async def process(self, job=None):
        await worker.process_connect_one(job or JOB, Mock())

    async def test_azerbaijani_voice_uses_central_key_history_and_text_send(self):
        await self.process()
        self.transcribe.assert_called_once()
        self.assertEqual(self.transcribe.call_args.args[:2], ('central-key', 'gpt-4o-mini-transcribe'))
        args = self.generate.call_args
        self.assertIn('Salam, qiyməti', str(args.args[3]))
        self.assertIn('[Audio transcript]', str(args.args[3]))
        self.assertEqual(args.kwargs['extra_context']['unprocessed_media_count'], 0)
        self.assertEqual(args.kwargs['extra_context']['latest_client_message'], 'Salam, qiyməti nə qədərdir?')
        self.assertEqual(self.reserve.call_args.kwargs['reserved_calls'], 2)
        self.assertEqual(self.usage.call_args.args[2]['total_tokens'], 31)
        self.assertEqual(self.rows[0]['transcript'], 'Salam, qiyməti nə qədərdir?')
        self.handoff.assert_not_called()
        self.send.assert_called_once()
        self.completed.assert_called_once()

    async def test_english_voice_keeps_original_language(self):
        self.transcribe.return_value = ('The application shows a synchronization error.', {})
        await self.process()
        self.assertIn('synchronization error', str(self.generate.call_args))
        self.assertNotIn('language', self.transcribe.call_args.kwargs)

    async def test_russian_voice_keeps_original_language(self):
        self.transcribe.return_value = ('Здравствуйте, сколько стоит программа?', {})
        await self.process()
        self.assertIn('сколько стоит программа', str(self.generate.call_args))
        self.assertNotIn('language', self.transcribe.call_args.kwargs)

    async def test_text_voice_text_retains_transcript_without_another_paid_call(self):
        self.rows = [row('text-1', 'text', 'Salam', '2026-10-16T00:00:00Z')]
        await self.process({**JOB, 'payload': {'message_id': 'text-1'}})
        self.rows.append(row())
        await self.process()
        self.rows.append(row('text-2', 'text', 'Bəs aylıq?', '2026-10-16T02:00:00Z'))
        await self.process({**JOB, 'payload': {'message_id': 'text-2'}})
        self.assertEqual(self.send.call_count, 3)
        self.transcribe.assert_called_once()
        self.assertIn('qiyməti nə qədərdir', str(self.generate.call_args.args[3]))
        self.assertIn('Bəs aylıq', str(self.generate.call_args.args[3]))

    async def test_fast_text_voice_text_coalescing_recovers_pending_audio(self):
        self.rows = [row('first', 'text', 'Salam', '2026-10-16T00:00:00Z'), row(),
                     row('latest', 'text', 'Bəs aylıq?', '2026-10-16T02:00:00Z')]
        await self.process({**JOB, 'payload': {'message_id': 'latest'}})
        self.transcribe.assert_called_once()
        self.send.assert_called_once()
        self.assertIn('qiyməti nə qədərdir', str(self.generate.call_args.args[3]))
        self.assertEqual(self.generate.call_args.kwargs['extra_context']['latest_client_message'], 'Bəs aylıq?')

    async def test_resume_does_not_transcribe_older_pending_audio(self):
        self.rows = [{**row(), 'voice_eligible': False},
                     row('new-text', 'text', 'Salam', '2026-10-16T02:00:00Z')]
        await self.process({**JOB, 'payload': {'message_id': 'new-text'}})
        self.transcribe.assert_not_called()
        self.send.assert_called_once()
        self.assertIn('Audio unavailable', str(self.generate.call_args.args[3]))

    async def test_delayed_pre_resume_voice_job_is_skipped(self):
        self.rows[0]['voice_eligible'] = False
        await self.process()
        self.transcribe.assert_not_called()
        self.generate.assert_not_called()
        self.send.assert_not_called()

    async def test_three_pending_audio_calls_are_bounded_and_usage_is_cumulative(self):
        self.rows = [row('v1', at='2026-10-16T00:01:00Z'),
                     row('v2', at='2026-10-16T00:02:00Z'),
                     row('v3', at='2026-10-16T00:03:00Z'),
                     row('latest', 'text', 'Salam', '2026-10-16T02:00:00Z')]
        await self.process({**JOB, 'payload': {'message_id': 'latest'}})
        self.assertEqual(self.transcribe.call_count, 3)
        self.assertEqual(self.reserve.call_args.kwargs['reserved_calls'], 4)
        self.assertEqual(self.audio_usage.call_args.args[2]['total_tokens'], 72)
        self.assertEqual(self.usage.call_args.args[2]['total_tokens'], 79)
        self.send.assert_called_once()

    async def test_failed_pending_voice_does_not_block_newer_text(self):
        self.rows = [row(), row('latest', 'text', 'Salam', '2026-10-16T02:00:00Z')]
        self.download.side_effect = voice.VoiceError('voice_media_unavailable')
        await self.process({**JOB, 'payload': {'message_id': 'latest'}})
        self.transcribe.assert_not_called()
        self.send.assert_called_once()
        self.assertIn('Audio unavailable', str(self.generate.call_args.args[3]))

    async def test_audio_failures_do_not_handoff_or_block_later_text(self):
        for code in ('voice_media_unavailable', 'voice_invalid_audio', 'voice_unsupported',
                     'voice_duration_limit', 'voice_size_limit'):
            with self.subTest(code=code):
                self.reserved.clear()
                self.claims.clear()
                self.rows = [row()]
                self.download.side_effect = voice.VoiceError(code)
                self.generate.reset_mock()
                await self.process()
                self.generate.assert_not_called()
                self.assertEqual(self.failed.call_args.args[2], code)
                self.rows.append(row('later', 'text', 'Hello', '2026-10-16T02:00:00Z'))
                await self.process({**JOB, 'payload': {'message_id': 'later'}})
                self.generate.assert_called_once()
                self.handoff.assert_not_called()

    async def test_duplicate_delivery_does_not_transcribe_or_reply_twice(self):
        await self.process()
        await self.process()
        self.transcribe.assert_called_once()
        self.send.assert_called_once()

    async def test_operator_takeover_during_transcription_stops_generation(self):
        def takeover(*args, **kwargs):
            self.active = False
            return 'real transcript', {}
        self.transcribe.side_effect = takeover
        await self.process()
        self.generate.assert_not_called()
        self.send.assert_not_called()
        self.superseded.assert_called_once()
        self.assertEqual(self.rows[0]['transcript'], 'real transcript')

    async def test_pause_before_start_prevents_any_paid_call(self):
        self.active = False
        await self.process()
        self.transcribe.assert_not_called()
        self.generate.assert_not_called()
        self.send.assert_not_called()

    async def test_provider_failure_has_no_invented_transcript(self):
        self.transcribe.side_effect = ChatAIProviderError(429, 'rate_limit')
        await self.process()
        self.assertEqual(self.rows[0]['body'], '')
        self.assertEqual(self.failed.call_args.args[2], 'voice_http_429_rate_limit')
        self.generate.assert_not_called()
        self.send.assert_not_called()

    async def test_timeout_after_submission_is_not_retried(self):
        self.transcribe.side_effect = TimeoutError('secret response')
        await self.process()
        self.reserved.clear()  # even a later run cannot repeat the claimed media
        await self.process()
        self.transcribe.assert_called_once()
        self.send.assert_not_called()

    async def test_outgoing_audio_does_not_enter_transcription(self):
        self.rows[0]['direction'] = 'outgoing'
        await self.process()
        self.download.assert_not_called()
        self.transcribe.assert_not_called()

    async def test_budget_exhausted_by_audio_prevents_generation(self):
        with patch.object(voice, 'generation_budget_available', return_value=False):
            await self.process()
        self.audio_usage.assert_called_once()
        self.generate.assert_not_called()

    async def test_same_message_identifier_is_not_cached_across_tenants(self):
        await self.process()
        self.rows = [row()]
        await self.process({**JOB, 'tenant_id': CHANNEL})
        self.assertEqual(self.transcribe.call_count, 2)


class VoiceBoundaries(unittest.TestCase):
    def connection(self, values):
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = values
        return connection, cursor

    def test_claim_is_committed_and_all_identifiers_are_scoped(self):
        conn, cur = self.connection([{'external_id': 'connect:' + MESSAGE}])
        with patch.object(voice, '_connect', return_value=conn), patch.object(voice, 'ensure_schema'):
            self.assertIsNone(voice._claim(JOB, RESERVATION, row(), 'transcriber'))
        sql, params = cur.execute.call_args.args
        self.assertIn('ON CONFLICT(tenant_id,channel_id,external_id) DO NOTHING', sql)
        self.assertIn('kommo_lead_id=%s', sql)
        self.assertIn(TENANT, params)
        self.assertIn(CHANNEL, params)
        self.assertIn('connect:' + MESSAGE, params)
        conn.__enter__.return_value.commit.assert_called_once()

    def test_claim_conflict_returns_saved_transcript_without_provider_call(self):
        conn, cur = self.connection([None, {'state': 'completed', 'body': 'cached'}])
        with patch.object(voice, '_connect', return_value=conn), patch.object(voice, 'ensure_schema'):
            self.assertEqual(voice._claim(JOB, RESERVATION, row(), 'model'), 'cached')
        self.assertIn('v.channel_id=%s::uuid', cur.execute.call_args.args[0])
        self.assertIn('m.kommo_lead_id=%s', cur.execute.call_args.args[0])

    def test_channel_and_conversation_mismatch_prevent_network_access(self):
        for route in (None, {'channel_id': TENANT, 'conversation_id': JOB['conversation_id']},
                      {'channel_id': CHANNEL, 'conversation_id': 'other'}):
            with self.subTest(route=route), patch.object(voice, '_connect', MagicMock()), \
                 patch.object(voice, 'connect_route', return_value=route), patch.object(voice, 'PinnedHTTPS') as https:
                with self.assertRaisesRegex(voice.VoiceError, 'voice_scope_mismatch'):
                    voice.download_audio(JOB, MESSAGE)
                https.assert_not_called()

    def test_network_is_pinned_bounded_and_checks_response_identity(self):
        conn, _ = self.connection([])
        response = Mock(status=200)
        headers = {'X-Connect-Channel-Id': CHANNEL, 'X-Connect-Message-Id': MESSAGE,
                   'Content-Type': 'audio/ogg; codecs=opus'}
        response.getheader.side_effect = lambda name, default=None: headers.get(name, default)
        response.read1.side_effect = [AUDIO, b'']
        http = Mock()
        http.getresponse.return_value = response
        route = {'channel_id': CHANNEL, 'conversation_id': JOB['conversation_id'], 'api_key': 'tenant-key'}
        with patch.object(voice, '_connect', return_value=conn), patch.object(voice, 'connect_route', return_value=route), \
             patch.object(voice, 'public_addresses', return_value=['8.8.8.8']), \
             patch.object(voice, 'PinnedHTTPS', return_value=http):
            self.assertEqual(voice.download_audio(JOB, MESSAGE), (AUDIO, 'ogg', 'audio/ogg'))
            headers['X-Connect-Channel-Id'] = TENANT
            with self.assertRaisesRegex(voice.VoiceError, 'voice_scope_mismatch'):
                voice.download_audio(JOB, MESSAGE)
        self.assertIn('/messages/' + MESSAGE + '/audio?', http.request.call_args.args[1])
        http.close.assert_called()

    def test_invalid_uuid_or_url_cannot_be_a_media_reference(self):
        with patch.object(voice, '_connect') as db:
            for value in ('https://127.0.0.1/private', '../secrets', ''):
                with self.assertRaises(voice.VoiceError):
                    voice.download_audio(JOB, value)
            db.assert_not_called()

    def test_final_send_lock_rejects_human_pause_and_revision_change(self):
        for current in ({'state': 'human', 'revision': 1}, {'state': 'paused', 'revision': 1},
                        {'state': 'active', 'revision': 2}, None):
            conn, cur = self.connection([current])
            with self.subTest(current=current), patch.object(transport, '_connect', return_value=conn), \
                 patch.object(transport, '_schema'), patch.object(transport, 'send_ai_reply') as send:
                result = transport.send_ai_reply_if_active(TENANT, 12, CHANNEL, JOB['conversation_id'], 'draft', RUN, 1)
            self.assertIsNone(result)
            send.assert_not_called()
            self.assertIn('FOR UPDATE OF c,cs', cur.execute.call_args.args[0])

    def test_failed_receipt_never_writes_transcript_or_another_company(self):
        conn, cur = self.connection([{'external_id': 'connect:' + MESSAGE}])
        with patch.object(voice, '_connect', return_value=conn):
            voice._finish(JOB, RESERVATION, row(), code='voice_invalid_audio')
        sql, params = cur.execute.call_args.args
        self.assertEqual(params[0], '')
        self.assertIn('tenant_id=%s::uuid', sql)
        self.assertIn('kommo_lead_id=%s', sql)
        self.assertIn('channel=%s', sql)
        self.assertEqual(params[2:], (TENANT, 12, 'connect:' + CHANNEL, 'connect:' + MESSAGE))

    def test_voice_reservation_counts_two_calls_and_obeys_daily_budget(self):
        config = {
            'metadata': {'daily_limit': 2}, 'configured_model': 'model',
            'tenant_status': 'active', 'settings_enabled': True, 'emergency_stop': False,
            'agent_enabled': True, 'conversation_state': 'active',
            'scopes': CONFIG['scopes'], 'conversation_revision': 1,
            'config_version': '1', 'settings_version': '1', 'name': 'agent', 'company_instructions': '',
            'instructions': '', 'language': 'az', 'style': 'short', 'agent_version': 1,
            'mode': 'automatic', 'auto_send_enabled': True, 'crm_actions_enabled': False,
            'max_replies_per_chat': 100,
        }
        for used, blocked in ((0, False), (1, True)):
            conn, cur = self.connection([None, config, None, {'calls': used}, {'calls': 0}, {'revision': 1}])
            with self.subTest(used=used), patch.object(runs, '_connect', return_value=conn), \
                 patch.object(runs, 'ensure_schema'), patch('tenant_ai_agent_admission.assert_job_allowed'), \
                 patch.object(runs, 'choose_api_credential', return_value=('central-key', 'platform')), \
                 patch.object(runs, 'platform_model_allowed', return_value=True), \
                 patch.object(runs, 'platform_token_limit', return_value=0):
                if blocked:
                    with self.assertRaises(TenantLinearError):
                        runs.reserve_run(JOB, pipeline_id='', stage_id='', model='model', reserved_calls=2)
                else:
                    result = runs.reserve_run(JOB, pipeline_id='', stage_id='', model='model', reserved_calls=2)
                    self.assertEqual(result['api_key'], 'central-key')
                    inserts = [call.args for call in cur.execute.call_args_list if 'INSERT INTO saas_ai_agent_runs' in call.args[0]]
                    self.assertEqual(inserts[0][1][-1], 2)
                    self.assertTrue(any("interval '3 minutes'" in call.args[0] for call in cur.execute.call_args_list))

    def test_usage_upserts_cumulative_totals_without_double_counting(self):
        conn, cur = self.connection([])
        with patch.object(runs, '_connect', return_value=conn), patch.object(runs, 'ensure_schema'):
            runs.record_token_usage(JOB, RESERVATION, {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}, cumulative=True)
        sql, params = cur.execute.call_args.args
        self.assertIn('ON CONFLICT(tenant_id,run_id)', sql)
        self.assertIn('GREATEST(saas_ai_agent_token_usage.total_tokens,EXCLUDED.total_tokens)', sql)
        self.assertEqual(params[-3:], (10, 2, 12))

    def test_audio_provider_usage_and_safe_failure(self):
        response = Mock(status_code=200)
        response.json.return_value = {'text': 'Salam', 'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}}
        with patch('tenant_chat_ai_provider.requests.post', return_value=response) as post:
            text, usage = transcribe('key', 'model', AUDIO, 'ogg', 'audio/ogg', with_usage=True)
        self.assertEqual((text, usage['total_tokens']), ('Salam', 12))
        self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_duplicate_signed_webhook_does_not_queue_again(self):
        conn, cur = self.connection([{'auto_send_enabled':True}, None, {'payload':{'channel_id':CHANNEL,'conversation_id':JOB['conversation_id']}}])
        with patch.object(adapter, '_connect', return_value=conn), patch.object(adapter, 'ensure_schema'), \
             patch.object(adapter, 'allowed') as allowed:
            count = adapter.enqueue(TENANT, channel_id=CHANNEL, conversation_id=JOB['conversation_id'],
                                    lead_id=12, message_id=MESSAGE, event_id='event-2', text='', message_type='audio')
        self.assertEqual(count, 0)
        allowed.assert_not_called()


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required for real decoder tests')
class RealAudio(unittest.TestCase):
    def test_real_ogg_opus_is_validated_without_conversion(self):
        self.assertEqual(voice.validate_audio(AUDIO, 'ogg', 'audio/ogg'), (AUDIO, 'ogg', 'audio/ogg'))

    def test_corrupted_audio_is_rejected(self):
        with self.assertRaisesRegex(voice.VoiceError, 'voice_invalid_audio'):
            voice.validate_audio(b'broken OGG data', 'ogg', 'audio/ogg')

    def test_unsupported_format_and_oversize(self):
        with self.assertRaisesRegex(voice.VoiceError, 'voice_unsupported'):
            voice.validate_audio(b'a', 'txt', 'text/plain')
        with self.assertRaisesRegex(voice.VoiceError, 'voice_size_limit'):
            voice.validate_audio(b'a' * (voice.MAX_BYTES + 1), 'ogg', 'audio/ogg')

    def test_actual_decoded_duration_limit(self):
        data = io.BytesIO()
        with wave.open(data, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b'\x00\x10' * 16000 * 2)
        with patch.object(voice, 'MAX_SECONDS', 1):
            with self.assertRaisesRegex(voice.VoiceError, 'voice_duration_limit'):
                voice.validate_audio(data.getvalue(), 'wav', 'audio/wav')

    def test_playlist_cannot_fetch_external_resources(self):
        with self.assertRaisesRegex(voice.VoiceError, 'voice_invalid_audio'):
            voice.validate_audio(b'#EXTM3U\nhttp://127.0.0.1/secrets\n', 'ogg', 'audio/ogg')


if __name__ == '__main__':
    unittest.main()
