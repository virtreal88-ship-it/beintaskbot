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
                    # Inspect each extraction media against this case. The
                    # shared extractor can contain unrelated media; only the
                    # existing verified case-boundary guard can admit it.
                    candidates.update(media)
                    for missing in linked-found:blocked.append({'case_id':case_id,'message_id':missing,'reason':'source_not_found'})
                candidates.update(str(m['id']) for m in plan.get('media',[]) if isinstance(m,dict) and m.get('id'))
                # The saved, bounded interval can contain attachments omitted by
                # the old extraction. Reuse the exact case-coverage guard.
                try:
                    chosen=cases.route(cur,profile,run['route'])[3]
                    interval=cases.case_media_coverage(cur,tenant,run,chosen,case.get('content') or {})
                    if interval['media_coverage'].get('boundary_status')=='verified':
                        candidates.update(str(m['id']) for m in interval['media_results'] if m.get('id'))
                except TenantLinearError as error:
                    if error.status not in {403,404,409}:raise
                for message_id in sorted(candidates):
                    pair=(case_id,message_id)
                    if pair in seen:continue
                    seen.add(pair)
                    if len(seen)>MAX_SOURCES:raise TenantLinearError('Too many sources; narrower selection required.',409)
                    ref={'case_id':case_id,'message_id':message_id,'expected_version':int(case['version'])}
                    try:inspect(cur,profile,case_id,message_id,case['version'],campaign_id)
                    except TenantLinearError as error:
                        if error.status not in {403,404,409}:raise
                        reason={'Case boundaries are unavailable.':'case_boundaries_unverified',
                          'The attachment is outside verified case boundaries.':'outside_verified_case',
                          'Source is outside the verified case route.':'outside_case_route'}.get(str(error),{403:'not_authorized',404:'source_not_found',409:'source_changed'}[error.status])
                        blocked.append({'case_id':case_id,'message_id':message_id,'reason':reason});continue
                    refs.append(ref)
            if not refs:return {'campaign_id':campaign_id,'campaign_version':campaign['version'],'case_count':case_count,'cases':case_list,'sources':[],'blocked':blocked,'snapshot_hash':None}
            result=preview(cur,profile,campaign_id,expected_version,refs,_max_sources=MAX_SOURCES)
            admitted=set()
            for item in sorted(result['sources'],key=lambda x:(x['case_id'],x['message_id'])):
                if item.get('extension_state')!='known_not_attempted':continue
                identity=(item['channel_id'],item['message_id'])
                if identity in admitted:
                    item['extension_state']='duplicate_case_source';continue
                admitted.add(identity)
                cur.execute("SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND route->'message_ids' @> %s::jsonb LIMIT 1",(tenant,campaign_id,__import__('json').dumps([item['message_id']])))
                if cur.fetchone():item['extension_state']='already_queued'
            result.update(case_count=case_count,cases=case_list,blocked=blocked,source_refs=refs)
            return result


def apply_owner(profile,campaign_id,data):
    required={'expected_version','source_refs','snapshot_hash','request_id','confirm_exact_preview'}
    if not isinstance(data,dict) or not required.issubset(data) or set(data)-required-{'current_byte_sources','acknowledge_incomplete_history','acknowledge_unknown_selected'} or data['confirm_exact_preview'] is not True:
        raise TenantLinearError('Confirm the exact preview.',400)
    # Client hashes are merely assertions. Re-fetch every exact source under the
    # owner's session before the locked transaction; dispatch checks bytes again.
    ack=data.get('acknowledge_incomplete_history',False)
    if type(ack) is not bool:raise TenantLinearError('Invalid acknowledgement.',400)
    unknown_ack=data.get('acknowledge_unknown_selected',False)
    if type(unknown_ack) is not bool:raise TenantLinearError('Invalid acknowledgement.',400)
    supplied=data.get('current_byte_sources',[])
    if not isinstance(supplied,list) or (unknown_ack and not supplied):raise TenantLinearError('Invalid byte snapshot.',400)
    if len(supplied)>10:raise TenantLinearError('Выберите не более 10 файлов за один раз.',400)
    refs=data['source_refs']
    if not isinstance(refs,list):raise TenantLinearError('Invalid sources.',400)
    verified={};unknown_selected=set()
    from tenant_case_current_media import verify_owner_source
    for item in supplied:
        if not isinstance(item,dict) or set(item)!={'case_id','message_id','case_version','sha256'}:
            raise TenantLinearError('Invalid byte snapshot.',400)
        key=(identifier(item['case_id']),identifier(item['message_id']))
        if key in verified or {'case_id':key[0],'message_id':key[1],'expected_version':item['case_version']} not in refs:
            raise TenantLinearError('Invalid byte snapshot source.',400)
        value=verify_owner_source(profile,campaign_id,{'expected_version':data['expected_version'],
          'case_id':key[0],'message_id':key[1],'case_version':item['case_version']})
        if value['current_sha256']!=item['sha256']:raise TenantLinearError('Current file changed; refresh.',409)
        if not value['continuation_supported']:
            raise TenantLinearError('Concurrent audio processing requires reconciliation.',409)
        state=value['attempt_reconciliation']['state']
        if value.get('saved_byte_results'):raise TenantLinearError('An exact-byte result is already available.',409)
        if unknown_ack:
            if value.get('uncertain_retry_available') is not True:
                raise TenantLinearError('Selected uncertain media changed or is active.',409)
            unknown_selected.add(key)
        elif state!='known_not_attempted' and not (state=='incomplete_history_eligible' and ack):
            raise TenantLinearError('Prior media outcome is unknown.',409)
        verified[key]=value['current_sha256']
    return apply(profile,campaign_id,data['expected_version'],refs,data['snapshot_hash'],data['request_id'],_max_sources=MAX_SOURCES,original_cases_only=True,verified_bytes=verified,acknowledge_incomplete_history=ack,unknown_selected=unknown_selected)
