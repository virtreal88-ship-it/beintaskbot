"""Explicit, fail-closed retries of employee extraction preflight failures.

No missing token receipt or client assertion is proof of non-dispatch. The
terminal run state is written by finish(attempted=False), including media
attempts. Monetary release commits separately, so its receipt is also required.
Historical uncapped requests without that receipt remain non-retryable.
"""
from tenant_linear_policy import TenantLinearError
import tenant_knowledge_candidates_store as store

ERROR='extraction_preflight_failed'
FLAGS=('allow_external_media','allow_paid_asr','allow_vision')


def eligible(cur,tenant,row):
    route=row.get('route') or {}
    target=route.get('training_target') or {}
    if (row.get('status')!='failed' or row.get('error_code')!=ERROR
        or not row.get('completed_at') or not target.get('submission_key')
        or route.get('provider')!='connect' or route.get('history_mode')!='channel'
        or any(route.get(flag) is not False for flag in FLAGS)
        or any(route.get(k) for k in ('media_grant_id','case_extension_id','photo_model',
            'campaign_photo_model','video_model','review','owner_retry_unknown','case_source_sha256'))):
        return False
    cur.execute('''SELECT r.state,r.error_code,r.generated_at,r.agent_id,r.model,
      u.input_tokens,u.output_tokens,u.total_tokens,u.reserved_tokens
      FROM saas_ai_agent_runs r JOIN saas_ai_agent_token_usage u
      ON u.tenant_id=r.tenant_id AND u.run_id=r.id
      WHERE r.tenant_id=%s::uuid AND r.id=%s::uuid''',(tenant,row['id']))
    run=cur.fetchone()
    if (not run or run['state']!='skipped' or run['error_code']!=ERROR
        or run['generated_at'] is not None or str(run['agent_id'])!=target.get('agent_id')
        or any(run[k]!=0 for k in ('input_tokens','output_tokens','total_tokens','reserved_tokens'))):
        return False
    cur.execute('SELECT 1 FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND extraction_id=%s::uuid LIMIT 1',(tenant,row['id']))
    if cur.fetchone():return False
    key='extraction:'+str(row['id'])
    cur.execute('''SELECT call_key,state,actual_microusd,completed_at,model,kind,campaign_id,grant_id
      FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid
      AND (call_key=%s OR call_key LIKE %s)''',(tenant,key,key+':%'))
    spends=cur.fetchall()
    # No retrospective inference from the current tenant cap. Require an
    # actual historical release receipt; refuse unexpected media reservations.
    if len(spends)!=1:return False
    spend=spends[0]
    return (spend['call_key']==key and spend['state']=='released'
        and spend['actual_microusd']==0 and spend['completed_at'] is not None
        and spend['model']==run['model'] and spend['kind']=='live_case_extraction'
        and spend['campaign_id'] is None and spend['grant_id'] is None)


def admit(cur,profile,tenant,parent_id,item,target):
    """Called only inside prepare's existing tenant-chat-ai transaction lock."""
    from tenant_operator_case_submission import is_submission,assignment
    if not is_submission(profile):raise TenantLinearError('Нет доступа.',403)
    parent=store.extraction_row(cur,tenant,parent_id)
    route=parent['route'];original=route.get('training_target') or {}
    if (original.get('submission_key')!=target['submission_key']
        or any(route.get(k)!=item['route'].get(k) for k in
            ('provider','channel_id','conversation_id','history_mode',*FLAGS,'dialog_ai_binding'))
        or [{k:v for k,v in r.items() if k!='text_limit'} for r in parent['source_refs']]
            !=[{k:v for k,v in r.items() if k!='text_limit'} for r in item['refs']]):
        raise TenantLinearError('Источник повторной попытки изменился.',409)
    assignment(cur,tenant,route['channel_id'],route['conversation_id'],original)
    store.evidence(cur,profile,parent)
    # A lost response or concurrent click returns the existing successor, not
    # another paid attempt. This query and insertion share the tenant lock.
    cur.execute('''SELECT id,route FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid
      AND route->>'retry_of'=%s ORDER BY created_at,id''',(tenant,parent_id))
    children=cur.fetchall()
    if len(children)>1:raise TenantLinearError('Повторная попытка требует проверки.',409)
    if children:
        child=children[0]
        if (child['route'].get('training_target') or {}).get('submission_key')!=target['submission_key']:
            raise TenantLinearError('Повторная попытка требует проверки.',409)
        return str(child['id'])
    if not eligible(cur,tenant,parent):
        raise TenantLinearError('Безопасный повтор до отправки модели не подтверждён.',409)
    return None


def decorate(profile,result,connect):
    """Read-only hint. Admission always checks again under the tenant lock."""
    request=result.get('request') or {}
    if not request.get('id'):return result
    with connect() as conn:
        with conn.cursor() as cur:
            tenant,_,_=store.authorize(cur,profile)
            row=store.extraction_row(cur,tenant,request['id'])
            store.evidence(cur,profile,row)
            retryable=eligible(cur,tenant,row)
    return {**result,'preflight_retry_available':bool(retryable)}
