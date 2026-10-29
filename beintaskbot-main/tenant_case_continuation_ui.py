"""Owner-session continuation of an existing fixed case-media grant."""
from tenant_linear_policy import TenantLinearError, identifier
from tenant_platform import _connect
import tenant_knowledge_candidates_store as cases
from tenant_case_source_inventory import inspect
from tenant_case_media_extension import preview, apply

MAX_SOURCES=250


def preview_owner(profile,campaign_id,expected_version):
    campaign_id=identifier(campaign_id)
    if type(expected_version) is not int or expected_version<1:
        raise TenantLinearError('Invalid campaign version.',400)
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,actor,policy=cases.authorize(cur,profile)
            if policy.profile.get('role')!='owner':raise TenantLinearError('Owner access required.',403)
            cur.execute('''SELECT c.version,c.approved_by,c.approved_at,c.progress,c.max_cost_microusd,
              g.status AS grant_status,g.cap_microusd,g.approved_at AS grant_approved_at,g.approved_by AS grant_approved_by
              FROM saas_knowledge_campaigns c JOIN saas_ai_media_grants g
                ON g.tenant_id=c.tenant_id AND g.id=c.id AND g.campaign_id=c.id
              WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid FOR SHARE OF c,g''',(tenant,campaign_id))
            campaign=cur.fetchone()
            if not campaign or str(campaign.get('approved_by'))!=str(actor):raise TenantLinearError('Original grant owner required.',403)
            if (not campaign.get('approved_at') or not campaign.get('grant_approved_at') or str(campaign.get('grant_approved_by'))!=str(actor) or campaign.get('grant_status') not in {'active','completed'}
                or campaign.get('cap_microusd')!=10000000 or campaign.get('max_cost_microusd')!=10000000):
                raise TenantLinearError('Original approved case grant required.',409)
            if campaign['version']!=expected_version:raise TenantLinearError('Campaign changed; refresh.',409)
            progress=campaign.get('progress') or {}
            if progress.get('mode')!='case_media_batch':raise TenantLinearError('Original case campaign required.',409)
            plans=[p for p in progress.get('case_plans',[]) if isinstance(p,dict) and p.get('case_id')]
            if len(plans)>250:raise TenantLinearError('Campaign case limit exceeded.',409)
            refs=[];seen=set();blocked=[];case_count=0;case_list=[]
            for plan in plans:
                case_id=identifier(plan['case_id'])
                cur.execute('SELECT id,version,status,content,extraction_id FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE',(tenant,case_id))
                case=cur.fetchone()
                if not case or case['status'] in {'archived','rejected'}:
                    blocked.append({'case_id':case_id,'reason':'case_unavailable'});continue
                case_count+=1
                case_list.append({'case_id':case_id,'title':str((case.get('content') or {}).get('title') or '')[:200],'status':case['status']})
                try:run=cases.extraction_row(cur,tenant,case['extraction_id'])
                except TenantLinearError as error:
                    if error.status not in {403,404,409}:raise
                    blocked.append({'case_id':case_id,'reason':'extraction_unavailable'});continue
                evidence={str(m) for m in (case.get('content') or {}).get('source_message_ids',[])}
                all_linked={str(m.get('id')) for m in (run.get('source_refs') or []) if isinstance(m,dict) and m.get('id')}
                linked=all_linked & evidence
                for missing in evidence-linked:blocked.append({'case_id':case_id,'message_id':missing,'reason':'source_attribution_missing'})
                candidates=set()
                if all_linked:
                    cur.execute('SELECT id::text AS id,message_type FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[])',(tenant,list(all_linked)))
                    rows=cur.fetchall();found={str(row['id']) for row in rows}
                    media={str(row['id']) for row in rows if row.get('message_type') in {'image','audio','voice','video','document'}}
                    candidates.update(media & linked)
                    original={str(m['id']) for m in plan.get('media',[]) if isinstance(m,dict) and m.get('id')}
                    for unscoped in media-linked-original:blocked.append({'case_id':case_id,'message_id':unscoped,'reason':'outside_case_evidence'})
                    for missing in linked-found:blocked.append({'case_id':case_id,'message_id':missing,'reason':'source_not_found'})
                candidates.update(str(m['id']) for m in plan.get('media',[]) if isinstance(m,dict) and m.get('id'))
                for message_id in sorted(candidates):
                    pair=(case_id,message_id)
                    if pair in seen:continue
                    seen.add(pair)
                    if len(seen)>MAX_SOURCES:raise TenantLinearError('Too many sources; narrower selection required.',409)
                    ref={'case_id':case_id,'message_id':message_id,'expected_version':int(case['version'])}
                    try:inspect(cur,profile,case_id,message_id,case['version'],campaign_id)
                    except TenantLinearError as error:
                        if error.status not in {403,404,409}:raise
                        blocked.append({'case_id':case_id,'message_id':message_id,'reason':{403:'not_authorized',404:'source_not_found',409:'source_changed'}[error.status]});continue
                    refs.append(ref)
            if not refs:return {'campaign_id':campaign_id,'campaign_version':campaign['version'],'case_count':case_count,'cases':case_list,'sources':[],'blocked':blocked,'snapshot_hash':None}
            result=preview(cur,profile,campaign_id,expected_version,refs,_max_sources=MAX_SOURCES)
            for item in result['sources']:
                if item.get('extension_state')!='known_not_attempted':continue
                cur.execute("SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND route->'message_ids' @> %s::jsonb LIMIT 1",(tenant,campaign_id,__import__('json').dumps([item['message_id']])))
                if cur.fetchone():item['extension_state']='already_queued'
            result.update(case_count=case_count,cases=case_list,blocked=blocked,source_refs=refs)
            return result


def apply_owner(profile,campaign_id,data):
    required={'expected_version','source_refs','snapshot_hash','request_id','confirm_exact_preview'}
    if not isinstance(data,dict) or set(data)!=required or data['confirm_exact_preview'] is not True:
        raise TenantLinearError('Confirm the exact preview.',400)
    return apply(profile,campaign_id,data['expected_version'],data['source_refs'],data['snapshot_hash'],data['request_id'],_max_sources=MAX_SOURCES,original_cases_only=True)
