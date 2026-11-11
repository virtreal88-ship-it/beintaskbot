"""Unused-agent archive: no transfer, side effects, history deletion or provider calls.

Reference writers take SHARE on the referenced agent before admitting a row.
Archive takes UPDATE before inspecting references. Thus a writer admitted first
is visible to the check; a writer admitted later sees the archive marker.
"""
from contextlib import nullcontext
import json

from tenant_linear_policy import TenantLinearError, identifier

# Structural relationship fields only, not arbitrary logs or prompt text.
REFERENCES = {
    'saas_ai_agents': ('expert_policy', 'response_policy', 'archived_replaced_by'),
    'saas_ai_agent_triggers': ('agent_id', 'condition', 'actions'),
    'saas_ai_conversation_state': ('agent_id',),
    'saas_ai_group_coordination': ('assignments',),
    'saas_ai_group_channel_legacy_permits': ('agent_id',),
    'saas_ai_legacy_permits': ('agent_id',),
    'saas_ai_agent_jobs': ('agent_id',),
    'saas_ai_agent_runs': ('agent_id',),
    'saas_ai_agent_token_usage': ('agent_id',),
    'saas_ai_agent_drafts': ('agent_id',),
    'saas_ai_agent_action_runs': ('agent_id',),
    'saas_ai_expert_tasks': ('principal_agent_id', 'expert_agent_id'),
    'saas_ai_test_sessions': ('agent_id',),
    'saas_ai_knowledge_sources': ('agent_id',),
    'saas_knowledge_campaigns': ('analysis_agent_id',),
    'saas_knowledge_candidate_sources': ('agent_id',),
    'saas_knowledge_quality_checks': ('agent_id',),
    'saas_ai_no_answer_state': ('agent_id',),
    'saas_ai_case_developer_dispatches': ('agent_id',),
    'saas_ai_simulation_configs': ('agent_id',),
    'saas_ai_simulation_runs': ('primary_agent_id','expert_results','events'),
    'saas_ai_simulation_usage': ('agent_id',),
    'saas_ai_channel_legacy_state': ('original',),
    'saas_ai_administrator_drafts': ('body', 'target'),
    'saas_ai_administrator_draft_versions': ('body', 'target'),
    'saas_ai_administrator_observations': ('payload',),
    'saas_connect_dialog_links': ('snapshot',),
    'saas_connect_direct_merge_backups': ('snapshot',),
    'saas_tenant_webhook_events': ('payload',),

    'saas_ai_instructor_jobs': ('primary_agent_id', 'expert_agent_id', 'progress'),
    'saas_ai_pilot_followups': ('agent_id',),
    'saas_dialog_consultation_replies': ('draft_agent_id',),
    'saas_dialog_audio_attempts': ('agent_id',),
    'saas_owner_asr_comparisons': ('agent_id',),
    'saas_instruction_video_offers': ('agent_id',),
    'saas_ai_learning_mcp_tokens': ('agent_id',),
    'saas_ai_learning_mcp_codes': ('agent_id',),
    'saas_ai_single_message_extraction_consents': ('agent_id',),
    'saas_ai_owner_publish_capabilities': ('agent_id',),
    'saas_ai_administrator_rollbacks': ('agent_id',),
    'saas_ai_administrator_trigger_rollbacks': ('agent_id',),
    'saas_ai_administrator_config_rollbacks': ('agent_id', 'before_patch'),
    'saas_ai_administrator_publication_grants': ('agent_id','before_snapshot','applied_snapshot'),
    'saas_ai_training_policies': ('agent_id',),
    'saas_knowledge_extractions': ('route',),
    'saas_knowledge_batches': ('params', 'routes', 'continuations'),
    'saas_knowledge_batch_items': ('route',),
    'saas_knowledge_campaign_fragments': ('route',),

}


def _reference_triggers(cur):
    cur.execute("""SELECT name,to_regclass(name) AS present,t.tgname,t.tgenabled,t.tgtype,t.tgargs,
      t.tgfoid=to_regprocedure('saas_ai_unused_archive_reference_fn()') AS correct_function
      FROM unnest(%s::text[]) names(name) LEFT JOIN pg_trigger t
        ON t.tgrelid=to_regclass(name) AND t.tgname='saas_ai_unused_archive_reference_v1'""", (list(REFERENCES),))
    return cur.fetchall()


def _correct_trigger(row):
    expected=b'\0'.join(field.encode() for field in REFERENCES[row['name']])+b'\0'
    return (row.get('tgenabled') in ('O','A') and row.get('tgtype')==23
            and row.get('correct_function') is True and bytes(row.get('tgargs') or b'')==expected)


def fences_ready(cur):
    cur.execute("""SELECT to_regclass('saas_ai_scoped_archives') IS NOT NULL
      AND to_regprocedure('saas_ai_unused_archive_reference_fn()') IS NOT NULL
      AND to_regprocedure('saas_ai_reference_ids(jsonb)') IS NOT NULL AS ready""")
    if not (cur.fetchone() or {}).get('ready'):
        return False
    return all(not row['present'] or _correct_trigger(row) for row in _reference_triggers(cur))


def install_existing(cur):
    """Also called by optional schema creators, before their first writes."""
    cur.execute("SELECT to_regclass('saas_ai_scoped_archives') AS present")
    if not (cur.fetchone() or {}).get('present'):
        return
    trigger='saas_ai_unused_archive_reference_v1'
    for relation in _reference_triggers(cur):
        if not relation['present'] or _correct_trigger(relation):
            continue
        table=relation['name'];columns=REFERENCES[table]
        # Identifiers and arguments are source constants, never request input.
        if relation['tgname']:
            cur.execute(f'DROP TRIGGER {trigger} ON {table}')
        args = ','.join("'" + column + "'" for column in columns)
        cur.execute(f'''CREATE TRIGGER {trigger} BEFORE INSERT OR UPDATE ON {table}
          FOR EACH ROW EXECUTE FUNCTION saas_ai_unused_archive_reference_fn({args})''')


def install_schema_references(conn):
    with conn.cursor() as cur:
        install_existing(cur)


def create_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_scoped_archives(
          tenant_id UUID NOT NULL,id UUID NOT NULL,archived_by BIGINT NOT NULL,
          archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,id),
          FOREIGN KEY(tenant_id,id) REFERENCES saas_ai_agent_history_identities(tenant_id,id) ON DELETE CASCADE)''')
        cur.execute('''CREATE OR REPLACE FUNCTION saas_ai_reference_ids(value jsonb)
          RETURNS uuid[] LANGUAGE sql IMMUTABLE AS $$
          WITH RECURSIVE nodes(v,k) AS (
            SELECT value,''::text UNION ALL
            SELECT child.v,child.k FROM nodes n CROSS JOIN LATERAL (
              SELECT v,n.k FROM jsonb_array_elements(CASE WHEN jsonb_typeof(n.v)='array' THEN n.v ELSE '[]'::jsonb END) a(v)
              UNION ALL
              SELECT v,k FROM jsonb_each(CASE WHEN jsonb_typeof(n.v)='object' THEN n.v ELSE '{}'::jsonb END) o(k,v)
            ) child
          ) SELECT COALESCE(array_agg(DISTINCT (v #>> '{}')::uuid),ARRAY[]::uuid[])
          FROM nodes WHERE jsonb_typeof(v)='string'
            AND k ~ '(^|_)agent_ids?$|^allowed_primary_ids$|^archived_replaced_by$'
            AND (v #>> '{}') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
          $$''')
        cur.execute('''CREATE OR REPLACE FUNCTION saas_ai_unused_archive_reference_fn()
          RETURNS trigger LANGUAGE plpgsql AS $$
          DECLARE data jsonb:=to_jsonb(NEW); selected jsonb:='{}'; field text;
            ids uuid[]; old_ids uuid[]; previous jsonb:='{}'; company uuid:=(data->>'tenant_id')::uuid;
          BEGIN
            FOREACH field IN ARRAY TG_ARGV LOOP selected:=selected || jsonb_build_object(field,data->field); END LOOP;
            ids:=saas_ai_reference_ids(selected);
            IF TG_OP='UPDATE' AND OLD.tenant_id=NEW.tenant_id THEN
              FOREACH field IN ARRAY TG_ARGV LOOP previous:=previous || jsonb_build_object(field,to_jsonb(OLD)->field); END LOOP;
              old_ids:=saas_ai_reference_ids(previous);
              ids:=ARRAY(SELECT unnest(ids) EXCEPT SELECT unnest(old_ids));
            END IF;
            IF cardinality(ids)=0 THEN RETURN NEW; END IF;
            PERFORM id FROM saas_ai_agents WHERE tenant_id=company AND id=ANY(ids) ORDER BY id FOR SHARE;
            IF EXISTS(SELECT 1 FROM saas_ai_scoped_archives WHERE tenant_id=company AND id=ANY(ids)) THEN
              RAISE EXCEPTION 'ai_agent_archived_reference' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
          END $$''')
        install_existing(cur)


def require_unused(cur, tenant, target, row):
    if row.get('enabled') or row.get('scopes'):
        raise TenantLinearError('ai_agent_archive_requires_unused_agent', 409)
    cur.execute('SELECT saas_ai_reference_ids(%s::jsonb) AS ids', (json.dumps([row.get('expert_policy'), row.get('response_policy')]),))
    if (cur.fetchone() or {}).get('ids'):
        raise TenantLinearError('ai_agent_archive_requires_unused_agent', 409)
    for table, columns in REFERENCES.items():
        cur.execute('SELECT to_regclass(%s) AS present', (table,))
        if not (cur.fetchone() or {}).get('present'):
            continue
        fields=','.join(f"'{column}',t.{column}" for column in columns)
        cur.execute(f'''SELECT 1 FROM {table} t WHERE t.tenant_id=%s::uuid
          AND %s::uuid=ANY(saas_ai_reference_ids(jsonb_build_object({fields}))) LIMIT 1''', (tenant,target))
        if cur.fetchone():
            # Never expose a reference count, type, target or preview to D-only.
            raise TenantLinearError('ai_agent_archive_requires_unused_agent', 409)


def archive_unused_agent(profile, agent_id, *, expected_version, connection=None):
    from tenant_ai_agents_store import _connect, ensure_schema, _authorize, _require_owner, _audit
    from tenant_ai_scoped_access import authorize
    target=identifier(agent_id)
    if type(expected_version) is not int or expected_version < 1:
        raise TenantLinearError('expected_version_required',400)
    with (nullcontext(connection) if connection is not None else _connect()) as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            authority=authorize(cur,profile,'delete',target)
            if authority is None:
                tenant,actor=_authorize(cur,profile)
                _require_owner(cur,tenant,actor)
                cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('ai-agent-config:'+tenant,))
            else:
                tenant,actor=authority
            cur.execute('''SELECT * FROM saas_ai_agents WHERE tenant_id=%s::uuid
              AND id=%s::uuid AND archived_at IS NULL FOR UPDATE''',(tenant,target))
            row=cur.fetchone()
            if not row:
                raise TenantLinearError('Agent tapılmadı.',404)
            if int(row['version']) != expected_version:
                raise TenantLinearError('agent_version_conflict',409)
            require_unused(cur,tenant,target,row)
            cur.execute('INSERT INTO saas_ai_scoped_archives(tenant_id,id,archived_by) VALUES(%s,%s,%s)',(tenant,target,actor))
            cur.execute('''UPDATE saas_ai_agents SET enabled=FALSE,archived_at=now(),archived_by=%s,
              version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid''',(actor,tenant,target))
            _audit(cur,tenant,actor,'ai_agent_archived_unused','ai_agent',target,{})
        conn.commit()
    return {'id':str(target),'version':expected_version+1,'archived':True}
