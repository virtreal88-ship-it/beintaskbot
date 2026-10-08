"""Durable prepare/execute/reconcile commands. No automatic background writes."""
import hashlib
import json
import uuid
from tenant_platform import _connect
from tenant_linear_commands_schema import ensure_commands_schema
from tenant_linear_context import context
from tenant_linear_policy import identifier, TenantLinearError
from tenant_linear_runtime_policy import visible, capabilities
from tenant_linear_task_inputs import edit_fields, text
from tenant_linear_tasks_provider import issue, mutate, ensure_comment
from tenant_linear_provider import team_catalog


def lock(cur, tenant, user, request, issue_id):
    for name in ('linear-request:' + tenant + ':' + str(user) + ':' + request, 'linear-issue:' + tenant + ':' + issue_id):
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (name,))


def fields_match(row, fields):
    names = {'stateId': ('state', 'id'), 'projectId': ('project', 'id'), 'assigneeId': ('assignee', 'id')}
    return bool(row) and all(((row.get(names[k][0]) or {}).get(names[k][1]) if k in names else row.get(k)) == v
        for k, v in fields.items() if k not in {'teamId'})


def authorized_row(profile, config, key, issue_id):
    row = issue(key, issue_id)
    if not row or row.get('id') != issue_id or not visible(profile, config, row):
        raise TenantLinearError('Tapşırıq tapılmadı və ya icazə yoxdur.', 404)
    return row


def intent_for(profile, config, key, data, issue_id):
    action = data['action']; rights = capabilities(profile, config)
    if action == 'create':
        if not rights['can_create']:
            raise TenantLinearError('Yaratmağa icazə yoxdur.', 403)
        team = team_catalog(key, config['team_id'])
        if not team or team['states']['pageInfo']['hasNextPage'] or config['workflow']['creation_state_id'] not in {s['id'] for s in team['states']['nodes']}:
            raise TenantLinearError('Yeni tapşırığın statusu bu komandada tapılmadı.', 409)
        fields = edit_fields(profile, config, key, data)
        fields.update(teamId=config['team_id'], stateId=config['workflow']['creation_state_id'])
        return {'action': action, 'fields': fields, 'before': None}
    row = authorized_row(profile, config, key, issue_id)
    if data.get('expected_updated_at') != row['updatedAt']:
        raise TenantLinearError('Tapşırıq dəyişib. Siyahını yeniləyin.', 409)
    comment = ''
    if action == 'edit':
        if not rights['can_edit']:
            raise TenantLinearError('Redaktəyə icazə yoxdur.', 403)
        fields = edit_fields(profile, config, key, data, row)
    elif action == 'status':
        if not rights['can_change_status']:
            raise TenantLinearError('Statusu dəyişməyə icazə yoxdur.', 403)
        target = identifier(data.get('state_id'))
        team = team_catalog(key, config['team_id'])
        if not team or team['states']['pageInfo']['hasNextPage'] or target not in {s['id'] for s in team['states']['nodes']}:
            raise TenantLinearError('Status bu komandaya aid deyil.')
        fields = {'stateId': target}
    elif action == 'button':
        options = capabilities(profile, config, row)['buttons']
        selected = next((b for b in options if b['id'] == data.get('button_id')), None)
        if not selected:
            raise TenantLinearError('Bu düyməyə icazə yoxdur.', 403)
        definition = next(b for b in config['workflow']['buttons'] if b['id'] == selected['id'])
        if definition['require_reason']:
            comment = text(data.get('reason'), 2000)
        fields = {'stateId': definition['to_state_id']}
    else:
        raise TenantLinearError('Əməliyyat düzgün deyil.')
    return {'action': action, 'fields': fields, 'before': row['updatedAt'], 'comment': comment,
            'button_id': data.get('button_id') if action == 'button' else None}


def recheck_rights(profile, config, intent):
    rights = capabilities(profile, config)
    required = {'create': 'can_create', 'edit': 'can_edit', 'status': 'can_change_status'}
    if intent['action'] in required and not rights[required[intent['action']]]:
        raise TenantLinearError('İcazələr dəyişib.', 403)
    if intent['action'] == 'button' and profile['role'] != 'owner':
        binding = config.get('members', {}).get(str(profile['telegram_id']), {})
        if intent['button_id'] not in binding.get('button_ids', []):
            raise TenantLinearError('Düymə icazəsi dəyişib.', 403)


def execute(session, data):
    if data.get('action') not in {'create', 'edit', 'status', 'button'}:
        raise TenantLinearError('Əməliyyat düzgün deyil.')
    request = identifier(data.get('request_id')); tenant = str(session['tenant_id']); user = int(session['telegram_id'])
    issue_id = str(uuid.uuid5(uuid.UUID(tenant), str(user) + ':' + request)) if data['action'] == 'create' else identifier(data.get('issue_id'))
    stamp = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # Phase 1 persists the exact intent BEFORE any remote write. Rollback after a
    # successful remote mutation cannot discard the identity of an uncertain request.
    with _connect() as conn:
        ensure_commands_schema(conn)
        with conn.cursor() as cur:
            lock(cur, tenant, user, request, issue_id)
            profile, config, key, version = context(cur, session)
            cur.execute('SELECT * FROM saas_linear_commands WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid', (tenant, user, request))
            previous = cur.fetchone()
            if previous:
                if previous['fingerprint'] != stamp:
                    raise TenantLinearError('Sorğu ID-si başqa dəyişiklikdə istifadə edilib.', 409)
                if previous.get('result'):
                    return previous['result']
                intent = previous['intent']
            else:
                if data.get('config_version') != version:
                    raise TenantLinearError('Linear qaydaları dəyişib. Yeniləyin.', 409)
                intent = intent_for(profile, config, key, data, issue_id)
                intent['config_version'] = version
                cur.execute('''INSERT INTO saas_linear_commands(tenant_id,actor_id,request_id,issue_id,fingerprint,intent)
                  VALUES(%s::uuid,%s,%s::uuid,%s::uuid,%s,%s::jsonb)''', (tenant, user, request, issue_id, stamp, json.dumps(intent)))
        conn.commit()
    # Phase 2 serializes app writers and reconciles provider success after timeouts.
    with _connect() as conn:
        with conn.cursor() as cur:
            lock(cur, tenant, user, request, issue_id)
            profile, config, key, version = context(cur, session)
            cur.execute('SELECT intent,result FROM saas_linear_commands WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid FOR UPDATE', (tenant, user, request))
            stored = cur.fetchone()
            if stored.get('result'):
                return stored['result']
            intent = stored['intent']
            if intent['config_version'] != version:
                raise TenantLinearError('Linear qaydaları dəyişib. Köhnə sorğu icra edilmədi.', 409)
            recheck_rights(profile, config, intent)
            if intent['action'] == 'create':
                # Use a deterministic provider UUID; a repeated create can never
                # become a second issue, even if the first response was lost.
                try:
                    changed = mutate(key, issue_id, intent['fields'], create=True)
                except TenantLinearError:
                    row = authorized_row(profile, config, key, issue_id)
                    if not fields_match(row, intent['fields']):
                        raise TenantLinearError('Yaratma nəticəsi qeyri-müəyyəndir. Eyni sorğunu yoxlayın.', 409) from None
                    changed = {'id': row['id'], 'updatedAt': row['updatedAt']}
            else:
                row = authorized_row(profile, config, key, issue_id)
                if fields_match(row, intent['fields']):
                    changed = {'id': row['id'], 'updatedAt': row['updatedAt']}
                elif row['updatedAt'] != intent['before']:
                    raise TenantLinearError('Tapşırıq başqa yerdə dəyişib. Dəyişiklik tətbiq edilmədi.', 409)
                else:
                    changed = mutate(key, issue_id, intent['fields'])
                if intent.get('comment'):
                    # Remote status and comment are not one provider transaction.
                    # A pending request reconciles the status and completes the same
                    # deterministic comment; never acknowledge until both succeed.
                    comment_id = str(uuid.uuid5(uuid.UUID(issue_id), tenant + ':' + str(user) + ':' + request))
                    ensure_comment(key, comment_id, issue_id, intent['comment'])
            if changed.get('id') != issue_id:
                raise TenantLinearError('Linear nəticəsinin ID-si uyğun deyil.', 502)
            result = {'issue_id': changed['id'], 'updated_at': changed['updatedAt']}
            cur.execute('''UPDATE saas_linear_commands SET result=%s::jsonb,completed_at=now()
              WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''', (json.dumps(result), tenant, user, request))
        conn.commit()
    return result
