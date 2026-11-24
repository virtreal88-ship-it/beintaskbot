"""Verified Operator account attached directly to an exact dialogue."""
import uuid

from tenant_beinsystems_operator import OperatorError
from tenant_beinsystems_operator_access import dialogue_chooser_enabled

TABLE = 'saas_operator_dialog_accounts'
_KEY = 'tenant_id=%s::uuid AND provider=%s AND lead_id=%s AND channel_id=%s AND conversation_id=%s'


def ready(cur):
    cur.execute('SELECT to_regclass(%s) IS NOT NULL AS ready',(TABLE,))
    return bool(cur.fetchone()['ready'])


def row(cur,tenant,route,*,lock=False):
    cur.execute(f'''SELECT id::text,account,generation,instance_id::text FROM {TABLE} WHERE {_KEY}'''
                + (' FOR UPDATE' if lock else ''),(tenant,*route))
    return cur.fetchone()


def snapshot(cur,tenant,policy,route,can_manage,*,record=None):
    from tenant_dialog_company_binding import _operator_choice_allowed
    can_choose = bool(can_manage and _operator_choice_allowed(cur,tenant,policy,route))
    projected = {'company_id':None,'company_name':None,'operator_account':None,
                 'can_manage':can_choose,'can_unlink':can_choose,'can_search_operator':can_choose}
    if policy.allows_action('operator_data','view'):
        found = record if record is not None else row(cur,tenant,route)
        if found and found['account']:
            projected.update(company_id=found['id'],company_name=found['account'],operator_account=found['account'])
    return projected


def save(cur,tenant,actor,route,current,account,generation=None,instance=None):
    previous=(current['account'],current['generation'],current['instance_id']) if current else None
    if previous==(account,generation,instance):
        return current
    ident=str(uuid.uuid4())
    if current:
        cur.execute(f'''UPDATE {TABLE} SET id=%s::uuid,account=%s,generation=%s,instance_id=%s::uuid,
          updated_by=%s,updated_at=now() WHERE {_KEY} AND id=%s::uuid
          RETURNING id::text,account,generation,instance_id::text''',
          (ident,account,generation,instance,actor,tenant,*route,current['id']))
    else:
        cur.execute(f'''INSERT INTO {TABLE}(tenant_id,provider,lead_id,channel_id,conversation_id,
          id,account,generation,instance_id,updated_by)
          VALUES(%s::uuid,%s,%s,%s,%s,%s::uuid,%s,%s,%s::uuid,%s) ON CONFLICT DO NOTHING
          RETURNING id::text,account,generation,instance_id::text''',
          (tenant,*route,ident,account,generation,instance,actor))
    saved=cur.fetchone()
    if not saved:
        raise OperatorError('Компания диалога изменилась. Обновите диалог.',409)
    return saved


def listed(cur,tenant,keys):
    if not keys or not dialogue_chooser_enabled(tenant) or not ready(cur):
        return {}
    providers,leads,channels,peers=zip(*keys)
    cur.execute(f'''WITH routes(provider,lead_id,channel_id,conversation_id) AS (
        SELECT * FROM unnest(%s::text[],%s::bigint[],%s::text[],%s::text[]))
      SELECT a.provider,a.lead_id,a.channel_id,a.conversation_id,a.account
      FROM routes r JOIN {TABLE} a USING(provider,lead_id,channel_id,conversation_id)
      WHERE a.tenant_id=%s::uuid AND a.account IS NOT NULL''',
      (list(providers),list(leads),list(channels),list(peers),tenant))
    return {(entry['provider'],entry['lead_id'],entry['channel_id'],entry['conversation_id']):
            (entry['account'],entry['account']) for entry in cur.fetchall()}
