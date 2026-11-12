"""Sparse per-employee Kommo pipeline access stored in the existing member policy."""
import hashlib
import json

from tenant_policy import policy_values, positive_id


def snapshot(workflow, target):
    policies = policy_values(workflow or {})
    members = policies.get('members') or {}
    settings = members.get(str(target['telegram_id']), {})
    pipelines = [row for row in (workflow or {}).get('pipelines', [])
                 if row.get('active', True) and (row.get('settings') or {}).get('provider') != 'beintaskbot']
    options = [{'pipeline_id': str(row['pipeline_id']), 'name': str(row.get('name') or '')}
               for row in pipelines if positive_id(row.get('pipeline_id'))]
    selected = [str(pid) for pid in settings.get('pipeline_ids', [])]
    user_id = str(settings.get('kommo_user_id') or '')
    version = hashlib.sha256(json.dumps([selected, user_id, options], sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return {'version': version, 'values': {'pipeline_ids': selected, 'kommo_user_id': user_id},
            'pipelines': options}


def apply_patch(cur, *, tenant_id, owner_id, target, workflow, patch):
    from tenant_member_store import MemberCardError
    from tenant_platform_core import _json
    if (not isinstance(patch, dict) or set(patch) != {'expected_version', 'changes'}
            or not isinstance(patch.get('expected_version'), str)
            or not isinstance(patch.get('changes'), dict) or not patch['changes']
            or set(patch['changes']) - {'pipeline_ids', 'kommo_user_id'}):
        raise MemberCardError('member_card_invalid', fields={'kommo_settings': 'invalid'})
    current = snapshot(workflow, target)
    if patch['expected_version'] != current['version']:
        raise MemberCardError('member_settings_conflict', 409, fields={'kommo_settings': 'version_conflict'})
    changes = patch['changes']
    ids = changes.get('pipeline_ids', current['values']['pipeline_ids'])
    if (not isinstance(ids, list) or any(not isinstance(pid, str) or not positive_id(pid) for pid in ids)
            or len(ids) != len(set(ids)) or set(ids) - {row['pipeline_id'] for row in current['pipelines']}):
        raise MemberCardError('member_card_invalid', fields={'kommo_settings': 'pipeline_ids_invalid'})
    user = changes.get('kommo_user_id', current['values']['kommo_user_id'])
    if not isinstance(user, str) or (user and not positive_id(user)):
        raise MemberCardError('member_card_invalid', fields={'kommo_settings': 'kommo_user_id_invalid'})
    policies = policy_values(workflow or {})
    members = dict(policies.get('members') or {})
    original = dict(members.get(str(target['telegram_id']), {}))
    original['pipeline_ids'] = ids
    if user:
        original['kommo_user_id'] = str(positive_id(user))
    else:
        original.pop('kommo_user_id', None)
    cur.execute('''UPDATE saas_tenant_workflow_policies
      SET value=jsonb_set(value,ARRAY[%s::text],%s::jsonb,TRUE),updated_by=%s,updated_at=now()
      WHERE tenant_id=%s::uuid AND policy_key='members' ''',
      (str(target['telegram_id']), _json(original), int(owner_id), tenant_id))
