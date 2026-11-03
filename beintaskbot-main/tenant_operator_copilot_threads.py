"""Transaction-scoped private Copilot thread primitives.

These helpers do not authorize callers or implement voice-session idempotency.
Callers must enforce dialog/member/agent checks, durable request idempotency and
source validation in the same transaction before appending. Never hold this
row lock while waiting for an external provider.
"""
import json


class ThreadCleared(Exception):
    pass


def append(cur, tenant, actor, channel, conversation, expected_clear_revision, entries):
    if type(expected_clear_revision) is not int or expected_clear_revision < 0:
        raise ValueError('invalid thread revision')
    if not isinstance(entries, list) or not entries or len(entries) > 20:
        raise ValueError('invalid thread entries')
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
    merged = (current['turns'] + entries)[-20:]
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
