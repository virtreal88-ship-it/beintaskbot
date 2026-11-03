"""Transaction-scoped private Copilot thread primitives.

These helpers do not authorize callers or implement voice-session idempotency.
Callers must enforce dialog/member/agent checks, durable request idempotency and
source validation in the same transaction before appending. Never hold this
row lock while waiting for an external provider.
"""
import json
import uuid


class ThreadCleared(Exception):
    pass


def _validated_entries(expected_binding, entries):
    """Reject unknown fields and unsafe voice metadata before touching a row.

    This establishes a storage shape, not voice authorization, replay or order.
    A future voice writer must enforce those independently before calling us.
    """
    if not isinstance(expected_binding, str) or not 1 <= len(expected_binding) <= 128:
        raise ValueError('invalid agent binding')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 20:
        raise ValueError('invalid thread entries')
    base = {'role','text','agent_binding'}
    voice = {'voice_session_id','turn_sequence','transcript_status','transcript_quality'}
    for entry in entries:
        if not isinstance(entry,dict) or not base.issubset(entry) or set(entry) - base - voice:
            raise ValueError('invalid thread entry')
        role = entry['role']; content = entry['text']
        if (not isinstance(role,str) or role not in {'employee','copilot'} or not isinstance(content,str) or
                len(content) > (1600 if role == 'employee' else 6000) or entry['agent_binding'] != expected_binding):
            raise ValueError('invalid thread entry')
        marked = bool(set(entry) & voice)
        if marked:
            if not voice.issubset(entry):
                raise ValueError('incomplete voice metadata')
            try:
                value = entry['voice_session_id']
                if not isinstance(value,str) or str(uuid.UUID(value)) != value:
                    raise ValueError()
            except (ValueError,AttributeError):
                raise ValueError('invalid voice session') from None
            sequence = entry['turn_sequence']
            status = entry['transcript_status']; quality = entry['transcript_quality']
            if (type(sequence) is not int or not 0 <= sequence < 2**31 or
                    status not in ('complete','partial','unavailable','unknown') or
                    quality not in ('automatic_unverified','human_verified','unknown') or
                    status in ('unavailable','unknown') and bool(content)):
                raise ValueError('invalid voice metadata')
    return entries


def append(cur, tenant, actor, channel, conversation, expected_clear_revision, expected_binding, entries):
    if type(expected_clear_revision) is not int or expected_clear_revision < 0:
        raise ValueError('invalid thread revision')
    entries = _validated_entries(expected_binding, entries)
    key = (tenant, actor, channel, conversation)
    # The INSERT establishes a lockable row even for two first-time writers.
    cur.execute('''INSERT INTO saas_operator_copilot_threads
      (tenant_id,requested_by,channel_id,conversation_id,turns,clear_revision)
      VALUES(%s::uuid,%s,%s,%s,'[]'::jsonb,0)
      ON CONFLICT(tenant_id,requested_by,channel_id,conversation_id) DO NOTHING''', key)
    cur.execute('''SELECT turns,clear_revision FROM saas_operator_copilot_threads
      WHERE tenant_id=%s::uuid AND requested_by=%s AND channel_id=%s AND conversation_id=%s FOR UPDATE''', key)
    current = cur.fetchone()
    if not current or type(current.get('clear_revision')) is not int or not isinstance(current.get('turns'), list):
        raise ValueError('invalid stored thread')
    if current['clear_revision'] != expected_clear_revision:
        raise ThreadCleared()
    # Match the existing Copilot rule: only the current assigned agent's
    # private turns survive a write. Include concurrent turns of that same
    # binding, rather than replacing them with a stale request snapshot.
    prior = [turn for turn in current['turns'] if isinstance(turn,dict) and turn.get('agent_binding') == expected_binding]
    merged = (prior + entries)[-20:]
    cur.execute('''UPDATE saas_operator_copilot_threads SET turns=%s::jsonb,updated_at=now()
      WHERE tenant_id=%s::uuid AND requested_by=%s AND channel_id=%s AND conversation_id=%s''',
                (json.dumps(merged, ensure_ascii=False), *key))


def clear(cur, tenant, actor, channel, conversation):
    cur.execute('''INSERT INTO saas_operator_copilot_threads
      (tenant_id,requested_by,channel_id,conversation_id,turns,clear_revision)
      VALUES(%s::uuid,%s,%s,%s,'[]'::jsonb,1)
      ON CONFLICT(tenant_id,requested_by,channel_id,conversation_id)
      DO UPDATE SET turns='[]'::jsonb,clear_revision=saas_operator_copilot_threads.clear_revision+1,updated_at=now()''',
                (tenant, actor, channel, conversation))


def cleanup_expired(cur, tenant):
    """Scrub old content while preserving durable clear fences.

    Acquire the existing tenant-chat-ai admission lock before cleanup. Call
    before reserving or reading a new text request. A future voice writer must
    share this protocol.
    Empty scope metadata and completed-run retention have separate policies.
    """
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-chat-ai:' + tenant,))
    cur.execute("""UPDATE saas_operator_copilot_threads t SET turns='[]'::jsonb,
      clear_revision=t.clear_revision+1,updated_at=now()
      WHERE t.tenant_id=%s::uuid AND t.updated_at<now()-interval '60 days'
      AND (t.turns IS NULL OR t.turns<>'[]'::jsonb)""", (tenant,))
    # Do not delete empty rows: a delayed text or voice writer can have read
    # their prior generation. Safe tombstone GC requires a separate proven
    # admission/retention protocol and is deliberately not implemented.
