"""Preview and queue case-bound sources under an existing fixed-budget grant.

Applying an exact preview leaves the campaign paused and reserves no funds.
"""
from tenant_linear_policy import TenantLinearError, identifier
import tenant_knowledge_candidates_store as cases
from tenant_case_source_inventory import inspect
from tenant_case_media_admission import _historical_attempt_reconciliation, _explicit_no_media_call


def _verified_byte_history(cur,tenant,message_id,source_hash,*,exclude_extraction_id=None,allow_incomplete=False):
    """Distinguish incomplete old records from any recorded or unresolved attempt."""
    import json
    history=_historical_attempt_reconciliation(cur,tenant,message_id,source_hash,exclude_extraction_id=exclude_extraction_id)
    incomplete=history.get('reason') in {'attempt_history_incomplete','historical_source_version_unverified'}
    if history.get('state')!='known_not_attempted' and not (allow_incomplete and incomplete):return history
    if incomplete:
        # Accept missing results or an explicit pre-dispatch consent refusal.
        # Neither establishes equality with the current bytes; all other
        # saved outcomes and media reservations remain blocked.
        cur.execute("""SELECT id::text AS id,status,source_refs,media_results FROM saas_knowledge_extractions
          WHERE tenant_id=%s::uuid AND (source_refs @> %s::jsonb OR media_results @> %s::jsonb)
          AND (%s::uuid IS NULL OR id<>%s::uuid) FOR SHARE""",
          (tenant,json.dumps([{'id':str(message_id)}]),json.dumps([{'id':str(message_id)}]),exclude_extraction_id,exclude_extraction_id))
        rows=cur.fetchall()
        if not rows:return history
        for row in rows:
            all_refs=row.get('source_refs');all_results=row.get('media_results')
            def blocked(reason):return {'state':'unknown','reason':reason,'matching_runs':len(rows)}
            if not isinstance(all_refs,list) or not isinstance(all_results,list):return blocked('prior_extraction_record_invalid')
            if any(not isinstance(r,dict) or not r.get('id') for r in all_refs+all_results):return blocked('prior_extraction_record_invalid')
            refs=[r for r in all_refs if str(r['id'])==str(message_id)]
            results=[r for r in all_results if str(r['id'])==str(message_id)]
            if row.get('status')!='completed':return blocked('prior_extraction_unfinished')
            if len(refs)!=1:return blocked('prior_source_reference_ambiguous')
            if results:
                if len(results)!=1 or refs[0].get('media_kind')!='image' or results[0].get('kind')!='image':
                    return blocked('prior_media_result_identity_mismatch')
                result=results[0]
                if result.get('status') in {'unknown','running','pending_processing'}:return blocked('prior_media_result_unknown')
                if result.get('status') in {'processed','partial'}:return blocked('prior_media_result_recorded')
                if result.get('code') not in {'vision_not_approved','external_media_permission_required'} or not _explicit_no_media_call(refs[0],result):
                    return blocked('prior_media_result_not_pre_dispatch')
            cur.execute("SELECT to_regclass('public.saas_ai_spend_reservations') AS relation")
            if not cur.fetchone().get('relation'):return history
            key='extraction:'+str(row['id'])
            cur.execute("""SELECT 1 FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid
              AND (call_key IN (%s,%s,%s) OR (call_key=%s AND state IN ('reserved','unknown'))) LIMIT 1""",
              (tenant,key+':asr',key+':vision',key+':photo',key))
            if cur.fetchone():return {'state':'unknown','reason':'prior_media_or_unresolved_reservation','matching_runs':len(rows)}
    cur.execute("""SELECT 1 FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid
      AND (source_refs @> %s::jsonb OR media_results @> %s::jsonb)
      AND (%s::uuid IS NULL OR id<>%s::uuid) AND status<>'completed' LIMIT 1""",
      (tenant,json.dumps([{'id':str(message_id)}]),json.dumps([{'id':str(message_id)}]),exclude_extraction_id,exclude_extraction_id))
    if cur.fetchone():return {'state':'unknown','reason':'prior_extraction_unfinished','matching_runs':history.get('matching_runs',0)}
    cur.execute("""SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid
      AND route->'message_ids' @> %s::jsonb AND (%s::uuid IS NULL OR id<>%s::uuid) LIMIT 1""",
      (tenant,json.dumps([str(message_id)]),exclude_extraction_id,exclude_extraction_id))
    if cur.fetchone():return {'state':'unknown','reason':'prior_fragment_exists','matching_runs':history.get('matching_runs',0)}
    cur.execute("""SELECT 1 FROM saas_connect_voice_receipts v JOIN saas_crm_messages m
      ON m.tenant_id=v.tenant_id AND m.channel='connect:'||v.channel_id::text AND m.external_id=v.external_id
      WHERE m.tenant_id=%s::uuid AND m.id=%s::uuid LIMIT 1""",(tenant,message_id))
    if cur.fetchone():return {'state':'unknown','reason':'prior_voice_receipt','matching_runs':history.get('matching_runs',0)}
    cur.execute("SELECT to_regclass('public.saas_knowledge_media_cache') AS relation")
    if cur.fetchone().get('relation'):
        cur.execute("""SELECT 1 FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid
          AND metadata->'owner_retry_source'->>'message_id'=%s LIMIT 1""",(tenant,str(message_id)))
        if cur.fetchone():return {'state':'unknown','reason':'prior_source_cache','matching_runs':history.get('matching_runs',0)}
    if incomplete:return {'state':'incomplete_history_eligible','reason':'no_recorded_media_attempt','matching_runs':len(rows)}
    return history


def preview(cur,profile,campaign_id,expected_campaign_version,sources,*,_internal=False,_max_sources=25):
    tenant,actor,policy=cases.authorize(cur,profile)
    if policy.profile.get('role')!='owner':raise TenantLinearError('Owner access required.',403)
    campaign_id=identifier(campaign_id)
    if type(expected_campaign_version) is not int or expected_campaign_version<1 or not isinstance(sources,list) or not 1<=len(sources)<=_max_sources:
        raise TenantLinearError('Invalid extension preview.',400)
    cur.execute('''SELECT c.version,c.status,c.max_cost_microusd,c.progress,c.max_calls,c.max_tokens,c.media_policy,c.reserved_calls,c.reserved_tokens,c.reserved_kinds,g.id AS grant_id,g.status AS grant_status,
      g.cap_microusd,g.approved_at FROM saas_knowledge_campaigns c JOIN saas_ai_media_grants g
      ON g.tenant_id=c.tenant_id AND g.campaign_id=c.id AND g.id=c.id
      WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid FOR SHARE OF c,g''',(tenant,campaign_id))
    campaign=cur.fetchone()
    if not campaign or not campaign.get('approved_at') or (campaign.get('progress') or {}).get('mode')!='case_media_batch':
        raise TenantLinearError('Original approved case grant required.',409)
    if campaign['version']!=expected_campaign_version:raise TenantLinearError('Campaign changed; refresh.',409)
    if campaign['grant_status'] not in {'active','completed'} or campaign['cap_microusd']!=10000000 or campaign['max_cost_microusd']!=campaign['cap_microusd']:
        raise TenantLinearError('Original grant unavailable.',409)
    cur.execute('''SELECT COALESCE(sum(COALESCE(actual_microusd,reserved_microusd)) FILTER(WHERE state='settled'),0) AS settled,
      COALESCE(sum(reserved_microusd) FILTER(WHERE state IN ('reserved','unknown')),0) AS held,
      COALESCE(sum(COALESCE(actual_microusd,reserved_microusd)) FILTER(WHERE state='released'),0) AS released_counted
      FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND grant_id=%s::uuid''',(tenant,campaign_id))
    totals=cur.fetchone()
    cur.execute('''SELECT COALESCE(sum(reserved_microusd) FILTER(WHERE state IN ('reserved','unknown')),0) AS held,
      COALESCE(sum(COALESCE(actual_microusd,reserved_microusd)) FILTER(WHERE state='settled'),0) AS settled
      FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND grant_id IS NULL''',(tenant,))
    prior=cur.fetchone();prior_held=int(prior['held']);prior_settled=int(prior['settled']);results=[];keys=set();snapshot=[]
    for source in sources:
        if not isinstance(source,dict) or set(source)!={'case_id','message_id','expected_version'}:
            raise TenantLinearError('Invalid case source.',400)
        case_id=identifier(source['case_id']);message_id=identifier(source['message_id']);key=(case_id,message_id)
        if key in keys:raise TenantLinearError('Duplicate case source.',400)
        keys.add(key)
        item=inspect(cur,profile,case_id,message_id,source['expected_version'],campaign_id,_internal=True)
        source_data=item.pop('_source')
        if item['grant_scope_match']:
            state='already_covered';history=None
        elif item.get('cached_status') in {'processed','partial'}:
            state='cached_bytes_unverified';history=None
        elif not item.get('source_version_verified'):
            state='source_version_unverified';history=None
        else:
            history=_historical_attempt_reconciliation(cur,tenant,message_id,source_data['source_hash'])
            state='known_not_attempted' if history.get('state')=='known_not_attempted' else 'previous_outcome_unknown'
        results.append({**item,'extension_state':state,'reconciliation':history})
        snapshot.append({'case_id':case_id,'message_id':message_id,'case_version':source['expected_version'],
          'channel_id':item['channel_id'],'lead_id':item['lead_id'],'kind':item['kind'],
          'source_version':source_data['source_version'],'source_hash':source_data['source_hash'],
          'conversation_id':source_data['conversation_id'],'occurred_at':source_data['occurred_at'],
          'state':state,'reconciliation':history})
    snapshot.sort(key=lambda x:(x['case_id'],x['message_id']))
    spent=int(totals['settled']);held=int(totals['held']);released_counted=int(totals['released_counted'])
    budget={'cap_microusd':int(campaign['cap_microusd']),'settled_microusd':spent,'held_microusd':held,
            'available_microusd':max(0,int(campaign['cap_microusd'])-spent-held-released_counted-prior_settled-prior_held),
            'prior_unknown_holds_microusd':prior_held,'prior_settled_microusd':prior_settled,
            'released_counted_microusd':released_counted}
    from tenant_knowledge_campaign_policy import reservation,fits
    amount=reservation(campaign['media_policy'])
    capacity={'calls_remaining':max(0,campaign['max_calls']-campaign['reserved_calls']),
      'tokens_remaining':max(0,campaign['max_tokens']-campaign['reserved_tokens']),
      'next_fragment_fits':bool(fits(campaign['media_policy'],campaign['reserved_kinds'],amount)
        and campaign['reserved_calls']+amount['calls']<=campaign['max_calls']
        and campaign['reserved_tokens']+amount['tokens']<=campaign['max_tokens'])}
    result={'campaign_id':campaign_id,'campaign_version':campaign['version'],'grant_status':campaign['grant_status'],
      'budget':budget,'operation_capacity':capacity,'sources':results,'snapshot_hash':cases.digest({'tenant_id':tenant,'actor_id':actor,'campaign_id':campaign_id,'campaign_version':campaign['version'],'budget':budget,'operation_capacity':capacity,'sources':snapshot}),
      'budget_basis':'current_snapshot_not_reservation','execution_authorized':False}
    if _internal:result['_snapshot']=snapshot
    return result


def apply(profile,campaign_id,expected_campaign_version,sources,snapshot_hash,request_id,*,_max_sources=25,original_cases_only=False,verified_bytes=None,acknowledge_incomplete_history=False):
    """Queue only proven unattempted sources, under the original fixed grant.

    The caller must already hold the separate extension OAuth scope and explicitly
    confirm this preview. This never starts the worker or releases unknown holds.
    """
    import uuid
    from datetime import timedelta
    from tenant_platform import _connect,_json
    from tenant_knowledge_candidates_policy import moment
    import tenant_knowledge_campaigns as campaigns
    campaign_id=identifier(campaign_id);request_id=identifier(request_id)
    if not isinstance(snapshot_hash,str) or len(snapshot_hash)!=64:raise TenantLinearError('Invalid preview.',400)
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,actor,policy=cases.authorize(cur,profile)
            if policy.profile.get('role')!='owner':raise TenantLinearError('Owner access required.',403)
            # Serialize with extraction admission before locking the campaign,
            # in the same order used by extraction preparation.
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-chat-ai:'+tenant,))
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('knowledge-campaign:'+tenant,))
            cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,campaign_id))
            campaign=cur.fetchone()
            if not campaign:raise TenantLinearError('Campaign unavailable.',404)
            if str(campaign.get('approved_by'))!=str(actor):raise TenantLinearError('Original grant owner required.',403)
            progress=dict(campaign.get('progress') or {});extensions=dict(progress.get('case_extensions') or {})
            if original_cases_only:
                allowed={str(p.get('case_id')) for p in progress.get('case_plans',[]) if isinstance(p,dict) and p.get('case_id')}
                if not isinstance(sources,list) or any(not isinstance(s,dict) or str(s.get('case_id')) not in allowed for s in sources):
                    raise TenantLinearError('Case is outside the original campaign.',403)
            prior=extensions.get(request_id)
            if prior:
                if prior.get('snapshot_hash')!=snapshot_hash:raise TenantLinearError('Request already used for a different preview.',409)
                return {'replayed':True,'count':len(prior.get('sources') or {}),'status':campaign['status']}
            if campaign['version']!=expected_campaign_version or campaign['status'] not in {'paused','stopped'}:
                raise TenantLinearError('Pause the original campaign and refresh.',409)
            cur.execute("SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND status='running' LIMIT 1",(tenant,))
            if cur.fetchone():raise TenantLinearError('A fragment is still running.',409)
            if campaign['status']=='stopped':
                cur.execute("SELECT 1 FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND status<>'stopped' AND id<>%s::uuid LIMIT 1",(tenant,campaign_id))
                if cur.fetchone():raise TenantLinearError('Another campaign is active.',409)
            cur.execute('SELECT status,cap_microusd,approved_by,approved_at FROM saas_ai_media_grants WHERE tenant_id=%s::uuid AND id=%s::uuid AND campaign_id=%s::uuid FOR UPDATE',(tenant,campaign_id,campaign_id))
            grant=cur.fetchone()
            if not grant or not grant.get('approved_at') or str(grant.get('approved_by'))!=str(actor) or grant['status'] not in {'active','completed'} or grant['cap_microusd']!=10000000:
                raise TenantLinearError('Original grant unavailable.',409)
            prepared=preview(cur,profile,campaign_id,expected_campaign_version,sources,_internal=True,_max_sources=_max_sources)
            if prepared['snapshot_hash']!=snapshot_hash:raise TenantLinearError('Preview changed; refresh.',409)
            if prepared['budget']['available_microusd']<=0:raise TenantLinearError('Original campaign budget is exhausted.',429)
            if not prepared['operation_capacity']['next_fragment_fits']:raise TenantLinearError('Original campaign operation allowance is exhausted.',409)
            selected=[];seen=set()
            for source in prepared['_snapshot']:
                digest=(verified_bytes or {}).get((source['case_id'],source['message_id']))
                if source['state'] in {'source_version_unverified','previous_outcome_unknown'} and digest:
                    history=_verified_byte_history(cur,tenant,source['message_id'],source['source_hash'],allow_incomplete=acknowledge_incomplete_history is True)
                    if history.get('state') in {'known_not_attempted','incomplete_history_eligible'}:
                        source={**source,'state':history['state'],'reconciliation':history,'current_sha256':digest}
                if source['state'] not in {'known_not_attempted','incomplete_history_eligible'}:continue
                source_key=(source['channel_id'],source['message_id'])
                if source_key in seen:continue
                seen.add(source_key)
                # Never replace or repeat any existing campaign fragment. The
                # original partial/unknown/success records and holds remain.
                cur.execute('''SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid
                  AND campaign_id=%s::uuid AND route->'message_ids' @> %s::jsonb LIMIT 1 FOR SHARE''',
                  (tenant,campaign_id,_json([source['message_id']])))
                if cur.fetchone():continue
                selected.append(source)
            if not selected:raise TenantLinearError('No proven unattempted new media sources.',409)
            channels=sorted({str(x) for x in campaign['sources']}|{x['channel_id'] for x in selected})
            if len(channels)>100:raise TenantLinearError('Too many source channels.',409)
            campaign={**campaign,'sources':channels}
            cur.execute('UPDATE saas_knowledge_campaigns SET sources=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(_json(channels),tenant,campaign_id))
            mapped={}
            for source in selected:
                at=moment(source['occurred_at'])
                if not at.tzinfo:raise TenantLinearError('Source timestamp unavailable.',409)
                signature=cases.digest({'campaign_id':campaign_id,'request_id':request_id,'source':source})
                route={'provider':'connect','channel_id':source['channel_id'],'conversation_id':source['conversation_id'],
                  'history_mode':'channel','message_ids':[source['message_id']],'period_start':at.isoformat(),
                  'period_end':(at+timedelta(microseconds=1)).isoformat(),'analysis_agent_id':str(campaign['analysis_agent_id']),
                  'source_version':source['source_version'],'case_id':source['case_id'],'case_version':source['case_version'],
                  'case_plan_hash':signature,'case_extension_id':request_id,'campaign_media_kind':source['kind']}
                if source.get('current_sha256'):route['case_source_sha256']=source['current_sha256']
                if source['kind']=='image' and campaign.get('photo_model'):route['campaign_photo_model']=campaign['photo_model']
                fragment=campaigns.enqueue(cur,profile,campaign,route)
                mapped[str(fragment)]={'source':source,'signature':signature}
            extensions[request_id]={'snapshot_hash':snapshot_hash,'owner_id':actor,'sources':mapped,'acknowledge_incomplete_history':acknowledge_incomplete_history is True}
            progress['case_extensions']=extensions
            if grant['status']=='completed':cur.execute("UPDATE saas_ai_media_grants SET status='active' WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,campaign_id))
            cur.execute("UPDATE saas_knowledge_campaigns SET status='paused',version=version+1,updated_at=now(),progress=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(_json(progress),tenant,campaign_id))
            cases._audit(cur,tenant,actor,'knowledge_campaign_case_extension','knowledge_campaign',campaign_id,{'count':len(mapped),'snapshot_hash':snapshot_hash})
        conn.commit()
    return {'replayed':False,'count':len(mapped),'status':'paused','provider_calls_started':False}


def dispatch_fence(cur,tenant,row):
    """Recheck the exact extension source and original owner at dispatch."""
    route=row.get('route') or {};progress=row.get('progress') or {}
    ext=(progress.get('case_extensions') or {}).get(str(route.get('case_extension_id') or '')) or {}
    saved=(ext.get('sources') or {}).get(str(row['id'])) or {};source=saved.get('source') or {}
    signature=cases.digest({'campaign_id':str(row['campaign_id']),'request_id':str(route.get('case_extension_id') or ''),'source':source})
    if (not source or source.get('state') not in {'known_not_attempted','incomplete_history_eligible'}
        or (source.get('state')=='incomplete_history_eligible' and ext.get('acknowledge_incomplete_history') is not True)
        or saved.get('signature')!=signature or signature!=route.get('case_plan_hash')):
        raise TenantLinearError('Case extension is unavailable.',409)
    profile={'tenant_id':tenant,'telegram_id':row['approved_by']}
    checked=inspect(cur,profile,source['case_id'],source['message_id'],source['case_version'],row['campaign_id'],_internal=True)
    identity=checked['_source']
    if ((not checked.get('source_version_verified') and not source.get('current_sha256')) or route.get('case_source_sha256')!=source.get('current_sha256') or str(ext.get('owner_id'))!=str(row['approved_by']) or route.get('case_id')!=source['case_id']
        or route.get('case_version')!=source['case_version'] or route.get('message_ids')!=[source['message_id']]
        or route.get('channel_id')!=source['channel_id'] or route.get('conversation_id')!=source['conversation_id']
        or route.get('source_version')!=source['source_version'] or identity['source_version']!=source['source_version']
        or identity['source_hash']!=source['source_hash'] or identity['occurred_at']!=source['occurred_at']):
        raise TenantLinearError('Case extension source changed.',409)
    if source.get('current_sha256'):
        history=_verified_byte_history(cur,tenant,source['message_id'],source['source_hash'],exclude_extraction_id=str(row['id']),allow_incomplete=source.get('state')=='incomplete_history_eligible')
    else:
        history=_historical_attempt_reconciliation(cur,tenant,source['message_id'],source['source_hash'],exclude_extraction_id=str(row['id']))
    if history.get('state')!=source['state']:
        raise TenantLinearError('Case media attempt history changed.',409)
    expected_photo=row.get('photo_model') if source['kind']=='image' else None
    if route.get('campaign_photo_model')!=expected_photo:raise TenantLinearError('Case photo model changed.',409)
    return source
