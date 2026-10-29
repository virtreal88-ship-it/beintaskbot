"""Owner-session byte verification for one exact existing case attachment."""
import hashlib
import threading
from tenant_linear_policy import TenantLinearError,identifier
from tenant_platform import _connect
import tenant_knowledge_candidates_store as cases
import tenant_case_source_inventory as inventory

_READ_SLOTS=threading.BoundedSemaphore(3)


def _authorized_snapshot(cur,profile,campaign_id,campaign_version,case_id,message_id,case_version):
    tenant,actor,policy=cases.authorize(cur,profile)
    if policy.profile.get('role')!='owner':raise TenantLinearError('Только владелец может проверять вложения.',403)
    campaign_id=identifier(campaign_id);case_id=identifier(case_id);message_id=identifier(message_id)
    if type(campaign_version) is not int or type(case_version) is not int or min(campaign_version,case_version)<1:
        raise TenantLinearError('Некорректная версия. Обновите состояние.',400)
    cur.execute('''SELECT c.version,c.approved_by,c.approved_at,c.progress,c.max_cost_microusd,
      g.status AS grant_status,g.cap_microusd,g.approved_at AS grant_approved_at,g.approved_by AS grant_approved_by
      FROM saas_knowledge_campaigns c JOIN saas_ai_media_grants g
      ON g.tenant_id=c.tenant_id AND g.id=c.id AND g.campaign_id=c.id
      WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid FOR SHARE OF c,g''',(tenant,campaign_id))
    row=cur.fetchone()
    if not row or str(row.get('approved_by'))!=str(actor) or str(row.get('grant_approved_by'))!=str(actor):
        raise TenantLinearError('Нет доступа к исходной обработке.',403)
    if (not row.get('approved_at') or not row.get('grant_approved_at') or row['grant_status'] not in {'active','completed'}
        or row.get('cap_microusd')!=10000000 or row.get('max_cost_microusd')!=10000000):
        raise TenantLinearError('Исходное разрешение недоступно.',409)
    if row['version']!=campaign_version:raise TenantLinearError('Обработка изменилась. Обновите состояние.',409)
    progress=row.get('progress') or {}
    if progress.get('mode')!='case_media_batch' or not any(str(p.get('case_id'))==case_id for p in progress.get('case_plans',[]) if isinstance(p,dict)):
        raise TenantLinearError('Кейс не входит в исходную обработку.',403)
    source=inventory.inspect(cur,profile,case_id,message_id,case_version,campaign_id,_internal=True)
    if not source['current_source_addressable']:
        raise TenantLinearError('Текущий источник нельзя адресовать через Connect.',409)
    return source


def verify_owner_source(profile,campaign_id,data):
    required={'expected_version','case_id','message_id','case_version'}
    if not isinstance(data,dict) or set(data)!=required:raise TenantLinearError('Некорректный источник.',400)
    args=(profile,campaign_id,data['expected_version'],data['case_id'],data['message_id'],data['case_version'])
    with _connect() as conn:
        with conn.cursor() as cur:before=_authorized_snapshot(cur,*args)
    if not _READ_SLOTS.acquire(blocking=False):
        error=TenantLinearError('Проверка файлов занята. Повторите позже.',429)
        error.code='owner_read_busy';error.retry_after=5;raise error
    try:
        payload,mime=inventory._fetch_current(profile,before)
    except TenantLinearError as error:
        stage=getattr(error,'source_stage','source_recheck')
        category={('rate_limit',429):'owner_read_rate_limit',('source_access',403):'source_access_denied',
          ('source_access',404):'source_route_unavailable',('connect_download',404):'connect_source_unavailable',
          ('connect_download',429):'connect_rate_limit',('connect_download',413):'connect_size_limit',
          ('connect_download',415):'connect_format_unsupported',('connect_download',502):'connect_fetch_failed'}.get((stage,error.status),'source_check_failed')
        if stage=='connect_download' and getattr(error,'code',None)=='connect_access_denied':category='connect_access_denied'
        safe=TenantLinearError('Не удалось проверить файл. Причина: '+category+'.',403 if category=='connect_access_denied' else error.status)
        safe.code=category;safe.retry_after=getattr(error,'retry_after',None);raise safe from None
    finally:
        _READ_SLOTS.release()
    if before['kind']=='image':
        from tenant_connect_images import signature
        if not signature(payload,mime):
            error=TenantLinearError('Не удалось проверить файл. Причина: connect_format_unsupported.',415)
            error.code='connect_format_unsupported';raise error
    with _connect() as conn:
        with conn.cursor() as cur:after=_authorized_snapshot(cur,*args)
    if before!=after:raise TenantLinearError('Кейс или источник изменился. Обновите состояние.',409)
    return {'case_id':before['case_id'],'message_id':before['message_id'],'case_version':before['case_version'],
      'provider':'connect','current_bytes_checked':True,'current_sha256':hashlib.sha256(payload).hexdigest(),
      'mime_type':mime,'content_signature_checked':before['kind']=='image','size_bytes':len(payload),
      'previous_bytes_verified':False,'provider_ai_calls':0}
