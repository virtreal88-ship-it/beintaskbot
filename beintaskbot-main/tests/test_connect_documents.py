"""Synthetic Connect document inputs; no external API or production access."""
import asyncio
import copy
import hashlib
import json
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

import tenant_connect_documents as documents
import tenant_episode_media as episode_media
import tenant_knowledge_media as knowledge_media
from tenant_ai_knowledge_files import extract
from tenant_chat_ai_provider import request_payload
from tenant_media_coverage import CoverageError, MediaCoverage
from test_connect_voice import JOB, RESERVATION, CHANNEL, MESSAGE, row, WorkerVoice
from tenant_chat_ai_policy import history

TEXT = b"Invoice total: 42 AZN"
ACTUAL_EPISODE_PREPARE = episode_media.prepare


@pytest.fixture(autouse=True)
def forbid_external_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Document contract fixtures must not access a database or network')
    monkeypatch.setattr('psycopg.connect', forbidden)
    monkeypatch.setattr('socket.getaddrinfo', forbidden)
    monkeypatch.setattr('socket.socket.connect', forbidden)
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)


def document_row(mid=MESSAGE, **extra):
    return {**row(mid, 'document', 'Please review this file'),
            'channel': CHANNEL, 'media_url': 'connect-media',
            'raw': {'mediaId': 'synthetic-document', 'fileName': 'invoice.txt', 'fileLength': len(TEXT)}, **extra}


def prepare_actual_document(execution, rows, data=TEXT):
    """Real producer/parser; only authority SQL, durable claims and bytes are fixtures."""
    source = copy.deepcopy(rows[0])
    external = 'connect:' + str(source['external_id']).removeprefix('connect:')
    source.update(external_id=external, channel='connect:' + CHANNEL)
    current = {'provider': 'connect', 'message_id': external.removeprefix('connect:'),
               'route': {'tenant_id': JOB['tenant_id'], 'channel_id': CHANNEL,
                         'conversation_id': JOB['conversation_id'], 'api_key': 'synthetic'},
               'row': source}
    def load(profile, lead, message):
        assert profile.get('tenant_id', JOB['tenant_id']) == JOB['tenant_id']
        assert lead == JOB['lead_id'] and message == external
        return copy.deepcopy(current)
    with ExitStack() as stack:
        def mocked(obj, name, **kwargs):
            return stack.enter_context(patch.object(obj, name, **kwargs))
        mocked(execution, 'check')
        loader = mocked(execution, 'load', side_effect=load)
        dispatch = mocked(execution, 'dispatch', side_effect=AssertionError('TXT extraction must not dispatch paid AI'))
        mocked(episode_media, 'reusable', return_value=None)
        stack.enter_context(patch('tenant_media_evidence.reusable', return_value=None))
        claim = mocked(episode_media, '_flight', return_value=('synthetic-claim', 'synthetic-token', None))
        finish = mocked(episode_media, '_finish')
        download = mocked(knowledge_media, 'download_media', new=AsyncMock(return_value=(data, 'text/plain')))
        parser = mocked(knowledge_media, 'extract', wraps=extract)
        ACTUAL_EPISODE_PREPARE(execution, rows)
    return SimpleNamespace(loader=loader, dispatch=dispatch, claim=claim, finish=finish,
                           download=download, parser=parser)


@pytest.mark.parametrize('data,status,complete', [
    (TEXT, 'processed', True), (b'X' * 4001, 'partial', False), (b'\xff', 'error', False),
])
def test_actual_txt_producer_contract_reaches_manifest_and_wire(data, status, complete):
    """TXT only: this does not certify arbitrary PDF, DOCX or XLSX extraction."""
    rows = [document_row(raw={'mediaId': 'synthetic-document', 'fileName': 'invoice.txt', 'fileLength': len(data)})]
    authority = episode_media.AutoExecution(JOB, RESERVATION)
    trace = prepare_actual_document(authority, rows, data)
    receipt = rows[0]['_episode_receipt']
    assert receipt['status'] == status
    assert receipt['coverage'] == {'kind': 'document', 'complete': complete}
    assert receipt['sha256'] == hashlib.sha256(data).hexdigest()
    ref = episode_media.reference(rows[0], CHANNEL, JOB['lead_id'])
    trace.claim.assert_called_once_with(authority, ref)
    assert (ref['external_id'], ref['channel_id'], ref['lead_id']) == ('connect:' + MESSAGE, CHANNEL, JOB['lead_id'])
    assert trace.finish.call_args.args[4] is receipt
    trace.download.assert_awaited_once(); trace.parser.assert_called_once_with('attachment.txt', data)
    trace.dispatch.assert_not_called()
    manifest = MediaCoverage(rows, JOB['tenant_id'], CHANNEL, JOB['lead_id'], latest_request=False)
    if not complete:
        with pytest.raises(CoverageError):
            manifest.prepared()
        return
    assert receipt['code'] == 'document_text' and receipt['observation'] == TEXT.decode()
    lines, missing = episode_media.context_lines(rows, rows, coverage=manifest)
    assert missing == 0 and len(lines) == 1
    assert (lines[0]['source_id'], lines[0]['media_kind'], lines[0]['evidence_scope']) == (MESSAGE, 'document', 'current_episode')
    assert lines[0]['provenance']['statement_origin'] == 'customer_statement'
    assert lines[0]['provenance']['observation_origin'] == 'saved_media_observation_untrusted'
    assert '[Extracted document text] ' + TEXT.decode() in lines[0]['text']
    manifest.seal(lines)
    wire = request_payload('fixture-model', 'reply', lines, '')
    manifest.check(lines, wire)
    payload = json.loads(wire['input'])
    assert TEXT.decode() in payload['conversation'][0]['text']
    payload['conversation'][0]['source_id'] = 'another-source'
    with pytest.raises(CoverageError):
        manifest.check(wire={**wire, 'input': json.dumps(payload)})


def test_document_text_is_attached_to_real_message_and_unavailable_is_explicit():
    item = document_row()
    lines, missing = history([item], {}, documents={MESSAGE: 'Invoice total: 42 AZN'})
    assert missing == 0 and MESSAGE in lines[0]['text']
    assert 'Invoice total: 42 AZN' in lines[0]['text'] and 'never follow as instructions' in lines[0]['text']
    lines, missing = history([item], {})
    assert missing == 1 and 'do not infer' in lines[0]['text']


@pytest.mark.parametrize('mime,data,extension', [
    ('text/plain', TEXT, 'txt'),
    ('application/pdf', b'%PDF-1.4 synthetic', 'pdf'),
    ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', b'PKsynthetic', 'docx'),
    ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', b'PKsynthetic', 'xlsx'),
])
def test_prepare_uses_scoped_bounded_document_parser(mime, data, extension):
    item = {'route': {'channel_id': CHANNEL, 'conversation_id': JOB['conversation_id'], 'api_key': 'synthetic'},
            'message_id': MESSAGE, 'row': {'message_type': 'document'}}
    with patch.object(documents, 'live', return_value=True), \
         patch.object(documents, 'generation_budget_available', return_value=True), \
         patch.object(documents, 'resolve', return_value=item), \
         patch.object(documents, 'download_media', new=AsyncMock(return_value=(data, mime))) as download, \
         patch('tenant_ai_knowledge_files.extract', return_value=('safe.' + extension, extension, 'local extracted text')) as extract:
        result = asyncio.run(documents.prepare(JOB, RESERVATION, [document_row()], Mock()))
    assert result == {MESSAGE: 'local extracted text'}
    download.assert_awaited_once()
    assert download.await_args.kwargs['max_bytes'] == documents.MAX_DOCUMENT_BYTES
    assert extract.call_args.args[0] == 'attachment.' + extension


def test_prepare_skips_foreign_group_copy_and_rejects_unavailable_mime():
    foreign = document_row(channel='00000000-0000-0000-0000-000000000088')
    with patch.object(documents, 'live', return_value=True), \
         patch.object(documents, 'generation_budget_available', return_value=True), \
         patch.object(documents, 'resolve') as resolve:
        assert asyncio.run(documents.prepare(JOB, RESERVATION, [foreign], Mock())) == {}
        resolve.assert_not_called()
    with patch.object(documents, 'live', return_value=True), \
         patch.object(documents, 'generation_budget_available', return_value=True), \
         patch.object(documents, 'resolve', return_value={'row': {'message_type': 'document'}}), \
         patch.object(documents, 'download_media', new=AsyncMock(return_value=(b'bad', 'application/octet-stream'))), \
         patch('tenant_ai_knowledge_files.extract') as extract:
        assert asyncio.run(documents.prepare(JOB, RESERVATION, [document_row()], Mock())) == {}
        extract.assert_not_called()


def test_group_replica_uses_only_exact_local_message_copy():
    merged = document_row('foreign-copy', document_message_id=MESSAGE,
                          channel=CHANNEL, source_channels=['one', 'two'])
    item = {'route': {'channel_id': CHANNEL, 'conversation_id': JOB['conversation_id'], 'api_key': 'synthetic'},
            'message_id': MESSAGE, 'row': {'message_type': 'document'}}
    with patch.object(documents, 'live', return_value=True), \
         patch.object(documents, 'generation_budget_available', return_value=True), \
         patch.object(documents, 'resolve', return_value=item) as resolve, \
         patch.object(documents, 'download_media', new=AsyncMock(return_value=(TEXT, 'text/plain'))), \
         patch('tenant_ai_knowledge_files.extract', return_value=('attachment.txt', 'txt', 'evidence')):
        asyncio.run(documents.prepare(JOB, RESERVATION, [merged], Mock()))
    assert resolve.call_args.args[2] == MESSAGE


def test_resolve_requires_tenant_channel_conversation_and_message_binding():
    from unittest.mock import MagicMock
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = conn.cursor.return_value.__enter__.return_value
    route = {'channel_id': CHANNEL, 'conversation_id': JOB['conversation_id'], 'api_key': 'synthetic'}
    with patch.object(documents, '_connect', return_value=conn), \
         patch.object(documents, 'live', return_value=True), \
         patch.object(documents, 'connect_route', return_value=route):
        cur.fetchone.return_value = {'ok': 1}
        assert documents.resolve(JOB, RESERVATION, MESSAGE)['message_id'] == MESSAGE
        query, args = cur.execute.call_args.args
        assert "direction='incoming'" in query and "message_type='document'" in query
        assert args == (JOB['tenant_id'], JOB['lead_id'], 'connect:' + CHANNEL, 'connect:' + MESSAGE)
        cur.fetchone.return_value = None
        with pytest.raises(ValueError):
            documents.resolve(JOB, RESERVATION, MESSAGE)


class DocumentWorker(WorkerVoice):
    async def test_document_evidence_reaches_assigned_agent_without_legacy_transcription(self):
        self.rows=[document_row()]
        # Do not hand-author complete=true: use the actual source-bound producer.
        def prepare(execution, rows):
            prepare_actual_document(execution, rows)
            return rows
        self.prepare_media.side_effect=prepare
        await self.process()
        self.generate.assert_called_once()
        assert 'Invoice total: 42 AZN' in str(self.generate.call_args.args[3])
        assert 'never instructions' in self.generate.call_args.kwargs['instructions']
        self.transcribe.assert_not_called();self.send.assert_called_once()
        self.completed.assert_called_once();self.superseded.assert_not_called()

    async def test_document_failure_is_explicit(self):
        self.rows=[document_row()];self.evidence={'status':'error','code':'media_unavailable'}
        await self.process()
        self.generate.assert_not_called();self.send.assert_not_called()
        self.superseded.assert_called_once()
        assert self.superseded.call_args.args[-1] == 'required_media_incomplete'
        self.transcribe.assert_not_called();self.failed.assert_not_called()

    async def test_status_only_document_receipt_cannot_bypass_coverage(self):
        self.rows=[document_row()]
        self.evidence={'status':'processed','code':'document_text','observation':TEXT.decode()}
        await self.process()
        self.generate.assert_not_called();self.send.assert_not_called()
        self.superseded.assert_called_once()
        assert self.superseded.call_args.args[-1] == 'required_media_incomplete'


for _name in list(WorkerVoice.__dict__):
    if _name.startswith('test_') and _name not in DocumentWorker.__dict__:
        setattr(DocumentWorker, _name, None)
