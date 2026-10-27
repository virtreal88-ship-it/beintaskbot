"""Bounded, read-only extraction of permitted Connect documents for agent context."""
import asyncio
import time
import uuid
from datetime import datetime, timedelta

from tenant_platform import _connect
from tenant_connect_transport import connect_route
from tenant_connect_media import download_media, DOCUMENT_TYPES
from tenant_connect_voice import active, generation_budget_available
from tenant_ai_group_coordination import job_allowed

MAX_DOCUMENTS = 2
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_TOTAL_TEXT = 12000
MAX_DOCUMENT_TEXT = 6000
TURN_SECONDS = 20
_download_slots = asyncio.Semaphore(2)
_EXTENSIONS = {
    'application/pdf': 'pdf',
    'text/plain': 'txt',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
}


class DocumentInactive(ValueError):
    pass


def live(job, reservation):
    if not active(job, reservation):
        return False
    from tenant_ai_agent_run_store import load_agent_config
    from tenant_ai_agent_connect import scope_matches
    config = load_agent_config(job)
    if not config or not scope_matches(config.get('scopes'), str(job['channel_id'])):
        return False
    with _connect() as conn:
        with conn.cursor() as cur:
            return job_allowed(cur, job)


def resolve(job, reservation, message):
    if not live(job, reservation):
        raise DocumentInactive('document_parent_inactive')
    message_id = str(uuid.UUID(str(message)))
    channel_id = str(uuid.UUID(str(job['channel_id'])))
    with _connect() as conn:
        with conn.cursor() as cur:
            route = connect_route(cur, str(job['tenant_id']), int(job['lead_id']))
            if (not route or route['channel_id'] != channel_id
                    or route['conversation_id'] != job['conversation_id']):
                raise ValueError('document_scope_mismatch')
            cur.execute("""SELECT 1 FROM saas_crm_messages
              WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s
                AND external_id=%s AND direction='incoming' AND message_type='document'""",
              (job['tenant_id'], int(job['lead_id']), 'connect:' + channel_id,
               'connect:' + message_id))
            if not cur.fetchone():
                raise ValueError('document_scope_mismatch')
    return {'route': route, 'message_id': message_id,
            'row': {'message_type': 'document'}}


async def prepare(job, reservation, rows, logger):
    """Extract only recent incoming files in this unanswered channel episode.

    Parsing is local and bounded; this function makes no model/provider calls.
    """
    episode = rows
    for index, row in enumerate(rows):
        if row.get('direction') == 'outgoing':
            episode = rows[index + 1:]
    candidates = [row for row in episode
                  if row.get('direction') == 'incoming' and row.get('message_type') == 'document']
    if not candidates:
        return {}
    if not await asyncio.to_thread(live, job, reservation):
        raise DocumentInactive('document_parent_inactive')
    if not await asyncio.to_thread(generation_budget_available, job, reservation):
        return {}
    if rows and rows[-1].get('happened_at'):
        try:
            end = datetime.fromisoformat(rows[-1]['happened_at'].replace('Z', '+00:00'))
            candidates = [row for row in candidates if row.get('happened_at') and
                timedelta(0) <= end - datetime.fromisoformat(row['happened_at'].replace('Z', '+00:00'))
                <= timedelta(minutes=10)]
        except (ValueError, TypeError):
            candidates = []
    result, used, deadline = {}, 0, time.monotonic() + TURN_SECONDS
    for row in candidates[-MAX_DOCUMENTS:]:
        # Group history is merged for context, but files must be fetched only
        # from an exact message copy in this job's tenant-owned channel.
        if row.get('channel') not in (None, str(job['channel_id'])) and not row.get('document_message_id'):
            continue
        external_id = str(row.get('external_id') or '')
        try:
            if not await asyncio.to_thread(live, job, reservation):
                raise DocumentInactive('document_parent_inactive')
            if not await asyncio.to_thread(generation_budget_available, job, reservation):
                break
            message_id = row.get('document_message_id') or external_id
            item = await asyncio.to_thread(resolve, job, reservation, message_id)
            async def bounded_download():
                async with _download_slots:
                    return await download_media(item, max_bytes=MAX_DOCUMENT_BYTES)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            data, mime = await asyncio.wait_for(bounded_download(), timeout=remaining)
            if mime not in DOCUMENT_TYPES or not data or len(data) > MAX_DOCUMENT_BYTES:
                continue
            if not await asyncio.to_thread(live, job, reservation):
                raise DocumentInactive('document_parent_inactive')
            from tenant_ai_knowledge_files import extract
            _name, _kind, text = await asyncio.to_thread(
                extract, 'attachment.' + _EXTENSIONS[mime], data)
            text = text[:min(MAX_DOCUMENT_TEXT, MAX_TOTAL_TEXT - used)]
            if text:
                result[external_id] = text
                used += len(text)
            if used >= MAX_TOTAL_TEXT:
                break
        except DocumentInactive:
            raise
        except Exception:
            logger.warning('Connect document unavailable or unreadable')
    return result
