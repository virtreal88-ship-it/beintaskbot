"""Campaign control and atomic fragment reservations over existing extraction receipts.

Preparing a campaign NEVER authorizes paid analysis. Explicit versioned approval
binds selected sources and bounded call/token reservations before the worker runs.
"""
import json
import uuid
from datetime import datetime, timezone
from tenant_platform import _connect, _json
from tenant_linear_policy import TenantLinearError, identifier
from tenant_knowledge_candidates_policy import MAX_TOKENS, period
import tenant_knowledge_candidates_store as store
from tenant_knowledge_campaign_schema import create_schema
from tenant_knowledge_campaign_policy import FIELDS, validate, reservation, fits


def _analysis_asr_model(cur, tenant, agent_id, *, resolve_asr=True):
    """Resolve the same owner-authorized ASR configuration used at dispatch."""
    from tenant_ai_agent_key_policy import platform_model
    cur.execute('''SELECT a.model,i.secrets,i.metadata FROM saas_ai_agents a
      LEFT JOIN saas_tenant_integrations i ON i.tenant_id=a.tenant_id AND i.provider='chat_ai'
      WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid''',(tenant,agent_id))
    row=cur.fetchone() or {};metadata=row.get('metadata') or {};company_key=''
    model=row.get('model') or metadata.get('model') or platform_model()
    if not model:
        raise TenantLinearError('analysis_model_unavailable',409)
    if not resolve_asr:
        return model,''
    from tenant_ai_agent_key_policy import choose_api_credential
    from tenant_platform import _fernet
    from tenant_connect_voice import transcription_model
    if row.get('secrets'):
        try:
            secret=json.loads(_fernet().decrypt(bytes(row['secrets'])))
            if secret.get('tenant_id')==tenant and secret.get('provider')=='chat_ai':
                company_key=str(secret.get('api_key') or '')
        except Exception:
            company_key=''
    _,credential_source=choose_api_credential(tenant,str(agent_id),'connect',company_key,model)
    try:
        asr_model=transcription_model({'credential_source':credential_source,'config':metadata})
    except Exception as error:
        raise TenantLinearError(f'asr_model_unavailable: {str(error)[:120]}',409) from None
    if not asr_model:
        raise TenantLinearError('asr_model_unavailable',409)
    return model,asr_model


def _configured_photo_model(cur, tenant, agent_id):
    """Use only the company's existing, explicitly selected photo model."""
    cur.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",(tenant,))
    metadata=(cur.fetchone() or {}).get('metadata') or {}
    model=str(metadata.get('photo_model') or '').strip()
    if not model:
        return None
    from tenant_ai_photo_model import MODELS, quote, select
    if model not in MODELS:
        raise TenantLinearError('photo_model_company_configuration_required',409)
    select(cur,{'AI_LEARNING_MCP_TENANT_ID':str(tenant),'AI_LEARNING_MCP_AGENT_ID':str(agent_id)},model)
    quote(model)
    return model


def prepare(profile, data):
    allowed={'request_id','channel_ids','analysis_agent_id','max_calls','max_tokens','case_id',
      'all_existing_cases','max_cost_microusd','video_model','acknowledge_prior_unknown'} | FIELDS
    if not isinstance(data,dict) or set(data)-allowed:
        raise TenantLinearError('Некорректные параметры.')
    key=identifier(data.get('request_id'))
    all_cases=data.get('all_existing_cases') is True
    if not all_cases and any(k in data for k in ('max_cost_microusd','video_model')):
        raise TenantLinearError('Monetary grant settings are available only for the fixed current-case snapshot.',400)
    if 'all_existing_cases' in data and type(data['all_existing_cases']) is not bool:
        raise TenantLinearError('Некорректный охват кейсов.')
    if 'acknowledge_prior_unknown' in data and type(data['acknowledge_prior_unknown']) is not bool:
        raise TenantLinearError('Некорректное подтверждение повторной попытки.')
    if data.get('acknowledge_prior_unknown') and not all_cases:
        raise TenantLinearError('Повторная попытка доступна только в общей кампании текущих кейсов.',400)
    channels=data.get('channel_ids')
    if not isinstance(channels,list) or (not all_cases and not 1<=len(channels)<=100) or (all_cases and len(channels)>100):
        raise TenantLinearError('Выберите источники.')
    channels=sorted(set(identifier(value) for value in channels))
    agent=identifier(data.get('analysis_agent_id'))
    calls=data.get('max_calls');tokens=data.get('max_tokens')
    if type(calls) is not int or not 1<=calls<=10000 or type(tokens) is not int or not MAX_TOKENS<=tokens<=160000000:
        raise TenantLinearError('Укажите ограниченный бюджет вызовов и токенов.')
    policy=validate(data,calls)
    with _connect() as conn:
        store.schema(conn);create_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,access=store.authorize(cur,profile)
            if all_cases and access.profile.get('role')!='owner':
                raise TenantLinearError('Only the company owner can prepare the one-time current-case media grant.',403)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('knowledge-campaign:'+tenant,))
            case_plan=None;batch_plan=None
            photo_model=None
            if all_cases:
                if type(data.get('max_cost_microusd')) is not int or data['max_cost_microusd']!=10_000_000:
                    raise TenantLinearError('Общий лимит новых расходов должен быть ровно 10 USD.',409)
                # The grant is a tenant-wide, one-time authorization. Check it
                # before re-planning current sources so edits or exhausted
                # budgets cannot turn a second prepare into an apparent reset.
                cur.execute('SELECT id FROM saas_ai_media_grants WHERE tenant_id=%s::uuid AND approved_at IS NOT NULL FOR UPDATE',(tenant,))
                if cur.fetchone():
                    raise TenantLinearError('A current-case media grant already exists and cannot be reset or topped up.',409)
                from tenant_case_media_admission import plan_existing_cases,existing_routes
                retry_ack=data.get('acknowledge_prior_unknown') is True
                batch_plan=plan_existing_cases(cur,profile,acknowledge_prior_unknown=retry_ack)
                if channels and channels!=batch_plan['channel_ids']:
                    raise TenantLinearError('Снимок каналов изменился.',409)
                channels=batch_plan['channel_ids']
                if not batch_plan['eligible_count']:
                    raise TenantLinearError('В текущем снимке нет доступных вложений.',409)
                if batch_plan['case_count'] and not data.get('video_model'):
                    if any(item.get('status')=='pending_consent' and any(m['kind']=='video' and not m['complete'] for m in item.get('media',[])) for item in batch_plan['cases']):
                        raise TenantLinearError('Для видео выберите отдельную модель с доступным тарифом.',409)
                if data.get('video_model'):
                    from tenant_ai_video_model import MODELS, require_video_tariff
                    from tenant_ai_spend_budget import media_price
                    pending_video=any(item.get('status')=='pending_consent' and any(m['kind']=='video' and not m['complete'] for m in item.get('media',[])) for item in batch_plan['cases'])
                    if data['video_model'] not in MODELS:
                        raise TenantLinearError('video_model_tariff_required',409)
                    if pending_video:
                        require_video_tariff(data['video_model'])
                        if media_price(data['video_model'],'vision_low') is None:
                            raise TenantLinearError('video_model_tariff_required',409)
                pending_image=any(item.get('status')=='pending_consent' and any(
                    media.get('kind')=='image' and not media.get('complete')
                    and media.get('status') in {'pending_consent','pending_retry','pending_processing'}
                    for media in item.get('media',[])) for item in batch_plan['cases'])
                if pending_image:
                    photo_model=_configured_photo_model(cur,tenant,agent)
                pending_asr=any(item.get('status')=='pending_consent' and any(
                    media.get('kind') in {'audio','voice','video'} and not media.get('complete')
                    and media.get('status') in {'pending_consent','pending_retry','pending_processing'}
                    for media in item.get('media',[])) for item in batch_plan['cases'])
                if pending_asr and policy['allow_paid_asr'] and policy['max_asr_calls']>0:
                    _,asr_model=_analysis_asr_model(cur,tenant,agent)
                    from tenant_ai_asr_model import quote as quote_asr
                    quote_asr(asr_model)
            if data.get('case_id'):
                from tenant_case_media_admission import plan, routes
                case_plan=plan(cur,profile,data['case_id'])
                if case_plan['status']!='pending_consent':
                    raise TenantLinearError(case_plan['reason'],409)
                if channels!=[case_plan['channel_id']] or not case_plan['media']:
                    raise TenantLinearError('Case source or attachments unavailable.',409)
                eligible_count=len(list(routes(case_plan,agent)))
                if not eligible_count:
                    raise TenantLinearError('No unprocessed eligible attachments; inspect saved case media status.',409)
            if not all_cases and (case_plan or data.get('allow_external_media') or data.get('allow_paid_asr')):
                cur.execute('SELECT 1 FROM saas_ai_media_grants WHERE tenant_id=%s::uuid LIMIT 1',(tenant,))
                if cur.fetchone():
                    raise TenantLinearError('Use the single current-case media grant; per-case campaign grants cannot bypass it.',409)
            cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,key))
            old=cur.fetchone()
            if old:
                old_case=(old.get('progress') or {}).get('case_id')
                batch_mismatch=bool(batch_plan and ((old.get('progress') or {}).get('snapshot_hash')!=batch_plan['snapshot_hash']
                    or old.get('max_cost_microusd')!=10_000_000 or old.get('video_model')!=data.get('video_model')
                    or old.get('photo_model')!=photo_model))
                if old['media_policy']!=policy or old['sources']!=channels or str(old['analysis_agent_id'])!=agent or old['max_calls']!=calls or old['max_tokens']!=tokens or old_case!=(case_plan or {}).get('case_id') or (case_plan and old['progress'].get('plan_hash')!=case_plan['plan_hash']) or batch_mismatch:
                    raise TenantLinearError('request_id уже используется.',409)
                return key
            for channel in channels:
                cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(tenant,channel))
                if not cur.fetchone(): raise TenantLinearError('Источник недоступен.',404)
            cur.execute("SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR SHARE",(tenant,agent))
            if not cur.fetchone(): raise TenantLinearError('Агент недоступен.',404)
            cur.execute("SELECT 1 FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND status<>'stopped'",(tenant,))
            if cur.fetchone(): raise TenantLinearError('Процесс уже подготовлен.',409)
            progress={'mode':'case_media','case_id':case_plan['case_id'],
              'plan_hash':case_plan['plan_hash'],'case_version':case_plan['case_version'],
              'attachment_count':len(case_plan['media']), 'eligible_count':eligible_count,
              'unavailable_count':sum(not item['complete'] for item in case_plan['media'])-eligible_count} if case_plan else {}
            if batch_plan:
                progress={'mode':'case_media_batch','snapshot_hash':batch_plan['snapshot_hash'],
                  'case_plans':batch_plan['cases'],'case_count':batch_plan['case_count'],
                  'eligible_count':batch_plan['eligible_count'],'reused_count':batch_plan['reused_count'],
                  'retry_unknown_count':batch_plan['retry_unknown_count'],
                  'acknowledge_prior_unknown':retry_ack,
                  'review_count':batch_plan['review_count'],'unknown_media_count':batch_plan['unknown_media_count'],
                  'unavailable_media_count':batch_plan['unavailable_media_count'],'all_existing_cases':True}
            cur.execute('''INSERT INTO saas_knowledge_campaigns
              (tenant_id,id,created_by,sources,analysis_agent_id,max_calls,max_tokens,media_policy,progress,max_cost_microusd,video_model,photo_model)
              VALUES(%s::uuid,%s::uuid,%s,%s::jsonb,%s::uuid,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)''',
              (tenant,key,actor,_json(channels),agent,calls,tokens,_json(policy),_json(progress),10_000_000 if batch_plan else None,data.get('video_model'),photo_model))
            if batch_plan:
                cur.execute('''INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status)
                  VALUES(%s::uuid,%s::uuid,%s::uuid,%s,10000000,'pending')''',(tenant,key,key,actor))
            store._audit(cur,tenant,actor,'knowledge_campaign_prepared','knowledge_campaign',key)
        conn.commit()
    return key


def control(profile, campaign_id, expected_version, action):
    if action not in {'pause','resume','stop'} or type(expected_version) is not int:
        raise TenantLinearError('Некорректное действие.')
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,actor,access=store.authorize(cur,profile)
            cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,identifier(campaign_id)))
            row=cur.fetchone()
            if not row: raise TenantLinearError('Процесс не найден.',404)
            if row['version']!=expected_version: raise TenantLinearError('Обновите состояние процесса.',409)
            if action=='resume' and (row['status']!='paused' or not row['approved_at']):
                raise TenantLinearError('Нужно отдельное подтверждение бюджета и источников.',409)
            if action=='pause' and row['status']!='running': raise TenantLinearError('Процесс не запущен.',409)
            if row['status']=='stopped': raise TenantLinearError('Процесс остановлен.',409)
            if action=='resume':
                cur.execute('''UPDATE saas_knowledge_campaign_fragments f SET status='pending',error_code=''
                  WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='blocked' AND error_code='limit_exceeded'
                  AND NOT EXISTS(SELECT 1 FROM saas_knowledge_extractions e WHERE e.tenant_id=f.tenant_id AND e.id=f.id)''',(tenant,campaign_id))
                if (row.get('progress') or {}).get('mode') not in {'case_media','case_media_batch','single_message'}:
                    cur.execute('''SELECT f.* FROM saas_knowledge_campaign_fragments f JOIN saas_knowledge_extractions e
                  ON e.tenant_id=f.tenant_id AND e.id=f.extraction_id WHERE f.tenant_id=%s::uuid AND f.campaign_id=%s::uuid
                  AND f.status IN ('partial','failed') AND NOT f.result ? 'retry_fragment_id' AND e.status IN ('completed','failed')
                  AND (e.status='failed' OR EXISTS(SELECT 1 FROM jsonb_array_elements(e.media_results) m WHERE m->>'status'<>'processed' OR m->>'audio_status'='unavailable_or_error'))
                  ORDER BY f.created_at,f.id LIMIT 4''',(tenant,campaign_id))
                    for failed in cur.fetchall():
                        retry=enqueue(cur,profile,row,{**failed['route'],'retry_of':str(failed['extraction_id'])})
                        cur.execute("UPDATE saas_knowledge_campaign_fragments SET result=result||%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(_json({'retry_fragment_id':retry}),tenant,failed['id']))
            state={'pause':'paused','resume':'running','stop':'stopped'}[action]
            cur.execute('UPDATE saas_knowledge_campaigns SET status=%s,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(state,tenant,campaign_id))
            if action=='stop' and (row.get('progress') or {}).get('mode')=='case_media_batch':
                cur.execute("UPDATE saas_ai_media_grants SET status='revoked',revoked_at=now() WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status IN ('pending','active')",(tenant,campaign_id))
            store._audit(cur,tenant,actor,'knowledge_campaign_'+action,'knowledge_campaign',campaign_id)
        conn.commit()


def enqueue(cur, profile, campaign, route):
    """Internal discovery adapter only; route always re-authorized, no browser IDs trusted."""
    tenant,_,_,_,binding,_=store.route(cur,profile,route)
    if tenant!=str(campaign['tenant_id']): raise TenantLinearError('Нет доступа.',403)
    period(route,True)
    if binding['channel_id'] not in campaign['sources']:
        raise TenantLinearError('Источник не утверждён.',403)
    route={**route,'approved_channel_ids':sorted(campaign['sources']),
      **{k:campaign['media_policy'].get(k,False) for k in ('allow_external_media','allow_paid_asr')}}
    if campaign.get('max_cost_microusd'):
        route['media_grant_id']=str(campaign['id'])
        if campaign.get('video_model'):
            route['video_model']=campaign['video_model']
        if campaign.get('photo_model'):
            media_kind=route.get('campaign_media_kind')
            if media_kind not in {'image','video','audio','voice','document'}:
                raise TenantLinearError('Case media kind changed; prepare a new snapshot.',409)
            if ((media_kind=='image' and route.get('campaign_photo_model')!=campaign.get('photo_model'))
                or (media_kind!='image' and route.get('campaign_photo_model') is not None)):
                raise TenantLinearError('Company photo model changed; prepare a new snapshot.',409)
        # This is only a server-side enqueue assertion, not persisted source data.
        route.pop('campaign_media_kind',None)
    route['allow_paid_asr']=bool(route['allow_paid_asr'] and campaign['media_policy'].get('max_asr_calls',0)>0)
    route['allow_vision']=bool(campaign['media_policy'].get('max_vision_calls',0) and route['allow_external_media'])
    key=store.digest(route);fragment=str(uuid.uuid5(uuid.UUID(str(campaign['id'])),key))
    cur.execute('''INSERT INTO saas_knowledge_campaign_fragments(tenant_id,campaign_id,id,source_key,route)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb) ON CONFLICT DO NOTHING''',
      (tenant,campaign['id'],fragment,key,_json(route)))
    return fragment


def _unknown_conflicts(cur, tenant, fragment, campaign):
    """Keep an uncertain call fenced unless both sources are provably disjoint.

    The spend ledger retains unknown reservations. This check only removes the
    tenant-wide scheduling hold for an independently identified attachment.
    """
    route=fragment['route']
    cur.execute('''SELECT f.id,f.route,e.source_refs FROM saas_knowledge_campaign_fragments f
      LEFT JOIN saas_knowledge_extractions e ON e.tenant_id=f.tenant_id
        AND e.id=COALESCE(f.extraction_id,f.id)
      WHERE f.tenant_id=%s::uuid AND f.status='unknown' ''',(tenant,))
    unknown=cur.fetchall()
    if not unknown:
        return False
    retry_parents=set()
    if route.get('retry_of'):
        from tenant_ai_single_photo_retry import authority
        try:
            consent=authority(cur,tenant,fragment['id'])
        except TenantLinearError:
            return True
        if str(consent['retry_of'])!=str(route['retry_of']):return True
        retry_parents.add(str(consent['retry_of']))
        if consent.get('photo_model'):
            from tenant_ai_single_photo_retry import validate
            from tenant_ai_single_message_extraction import _source_for_row
            cfg={'AI_LEARNING_MCP_TENANT_ID':tenant,'AI_LEARNING_MCP_CHANNEL_ID':str(consent['channel_id'])}
            checked=validate(cur,consent,_source_for_row(cur,cfg,consent))
            retry_parents.update(checked['ancestors'])
    def scoped_id(value):
        try:
            return str(uuid.UUID(value)) if isinstance(value,str) else None
        except ValueError:
            return None

    ids=route.get('message_ids')
    channel=scoped_id(route.get('channel_id'))
    version=route.get('source_version')
    if ((campaign.get('progress') or {}).get('mode') not in {'single_message','case_media','case_media_batch'}
        or not isinstance(ids,list) or len(ids)!=1 or not scoped_id(ids[0]) or not channel
        or not isinstance(version,str) or len(version)!=64 or any(c not in '0123456789abcdef' for c in version.lower())):
        return True
    selected=(channel,scoped_id(ids[0]))
    batch_retry=(route.get('owner_retry_unknown') is True
        and (campaign.get('progress') or {}).get('mode')=='case_media_batch'
        and (campaign.get('progress') or {}).get('acknowledge_prior_unknown') is True
        and str(route.get('media_grant_id') or '')==str(campaign.get('id') or ''))
    for old in unknown:
        if str(old['id']) in retry_parents:
            continue  # Only this exact, owner-acknowledged parent; its hold stays.
        old_route=old['route'] or {}
        old_ids=old_route.get('message_ids')
        identities=set()
        if old_ids is not None:
            if not isinstance(old_ids,list) or not old_ids or any(not scoped_id(value) for value in old_ids):
                return True
            old_channel=scoped_id(old_route.get('channel_id'))
            old_selected=selected[1] in {scoped_id(value) for value in old_ids}
            if old_selected and not (batch_retry and old_channel==channel):
                return True
            if old_channel:
                identities.update((old_channel,scoped_id(value)) for value in old_ids)
        refs=old['source_refs']
        if refs is not None:
            if not isinstance(refs,list) or not refs:
                return True
            for ref in refs:
                if not isinstance(ref,dict):
                    return True
                ref_id=scoped_id(ref.get('id'))
                ref_channel=scoped_id(ref.get('channel_id'))
                if not ref_id or not ref_channel:
                    return True
                if ref_id==selected[1]:
                    if not (batch_retry and ref_channel==channel):return True
                    continue
                identities.add((ref_channel,ref_id))
        if not identities or selected in identities and not batch_retry:
            return True  # Same attachment/version replacement or unresolved source.
        if selected in identities and batch_retry:
            # The exact current source has a one-time owner acknowledgement;
            # prior holds stay untouched and a grant-scoped cache claim prevents
            # another retry under this same grant.
            continue
    return False


def claim(tenant, campaign_id):
    """One call reservation per tenant. Unknown attempts never auto-retry/refund."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('knowledge-campaign:'+tenant,))
            cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,campaign_id))
            campaign=cur.fetchone()
            if not campaign or campaign['status']!='running' or not campaign['approved_at']: return None
            profile={'tenant_id':tenant,'telegram_id':campaign['created_by']}
            store.authorize(cur,profile)
            cur.execute("UPDATE saas_knowledge_campaign_fragments SET status='unknown',error_code='interrupted',updated_at=now() WHERE tenant_id=%s::uuid AND status='running' AND lease_until<now()",(tenant,))
            cur.execute("SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND status='running' LIMIT 1",(tenant,))
            if cur.fetchone():
                conn.commit();return None
            cur.execute("SELECT 1 FROM saas_knowledge_quality_checks q JOIN saas_ai_test_turns t ON t.tenant_id=q.tenant_id AND t.id=q.id WHERE q.tenant_id=%s::uuid AND t.state IN ('generating','unknown') LIMIT 1",(tenant,))
            if cur.fetchone():
                conn.commit();return None
            cur.execute("SELECT * FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='pending' ORDER BY created_at,id LIMIT 1 FOR UPDATE",(tenant,campaign_id))
            fragment=cur.fetchone()
            if not fragment: return None
            if _unknown_conflicts(cur,tenant,fragment,campaign):
                conn.commit();return None
            store.route(cur,profile,fragment['route'])
            amount=reservation(campaign['media_policy'])
            if not fits(campaign['media_policy'],campaign['reserved_kinds'],amount) or campaign['reserved_calls']+amount['calls']>campaign['max_calls'] or campaign['reserved_tokens']+amount['tokens']>campaign['max_tokens']:
                cur.execute("UPDATE saas_knowledge_campaigns SET status='budget_exhausted',version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,campaign_id))
                conn.commit();return None
            token=str(uuid.uuid4())
            cur.execute('UPDATE saas_knowledge_campaigns SET reserved_calls=reserved_calls+%s,reserved_tokens=reserved_tokens+%s,reserved_kinds=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(amount['calls'],amount['tokens'],_json({k:campaign['reserved_kinds'].get(k,0)+amount[k] for k in ('main','asr','vision')}),tenant,campaign_id))
            cur.execute("UPDATE saas_knowledge_campaign_fragments SET status='running',reservation=%s::jsonb,lease_token=%s::uuid,lease_until=now()+interval '360 seconds',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(_json(amount),token,tenant,fragment['id']))
        conn.commit()
    return {**fragment,'reservation':amount,'lease_token':token,'profile':profile}


def get(profile, campaign_id=None):
    with _connect() as conn:
        store.schema(conn);create_schema(conn)
        with conn.cursor() as cur:
            tenant,_,_=store.authorize(cur,profile)
            # The schema enforces at most one non-stopped campaign per tenant.
            # Restore that campaign before newer stopped history; an explicit id
            # remains exact and is never substituted with another campaign.
            cur.execute('''SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid
              AND (%s::uuid IS NULL OR id=%s::uuid)
              ORDER BY CASE WHEN status <> 'stopped' THEN 0 ELSE 1 END, created_at DESC, id DESC LIMIT 1''',(tenant,campaign_id,campaign_id))
            row=cur.fetchone()
            if not row:return {'campaign':None}
            cur.execute('''SELECT status,count(*) AS n FROM saas_knowledge_campaign_fragments
              WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid GROUP BY status''',(tenant,row['id']))
            counts={r['status']:r['n'] for r in cur.fetchall()}
            cur.execute("SELECT count(*) AS n FROM saas_knowledge_campaign_sources WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='pending'",(tenant,row['id']))
            sources_pending=cur.fetchone()['n']
            progress=dict(row['progress'])
            if progress.get('mode') in {'case_media','case_media_batch'}:
                cur.execute("""SELECT count(*) AS n FROM saas_knowledge_campaign_fragments
                  WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='completed'
                    AND extraction_id IS NOT NULL AND error_code=''""",(tenant,row['id']))
                succeeded=int(cur.fetchone()['n'])
                eligible=int(progress.get('eligible_count') or 0)
                if row['status']=='stopped' and (counts.get('pending') or counts.get('running')):
                    progress['phase']='cancelled'
                elif (progress.get('unavailable_count') or progress.get('review_count') or any(counts.get(state) for state in ('unknown','blocked','failed','partial'))
                      or row['status']=='stopped' and (eligible==0 or succeeded!=eligible)):
                    progress['phase']='incomplete'
                elif row['status']=='stopped':progress['phase']='completed'
                else:progress['phase']='pending_consent' if not row['approved_at'] else 'processing'
            elif progress and not sources_pending and not counts.get('pending') and not counts.get('running'):
                progress['phase']='waiting_completion'
            cur.execute('''SELECT count(DISTINCT c.id) AS cards FROM saas_knowledge_candidates c
              JOIN saas_knowledge_campaign_fragments f ON f.tenant_id=c.tenant_id AND f.extraction_id=c.extraction_id
              WHERE f.tenant_id=%s::uuid AND f.campaign_id=%s::uuid''',(tenant,row['id']))
            cards=cur.fetchone()['cards']
            cur.execute('''SELECT COALESCE(sum(u.total_tokens),0) AS tokens FROM saas_ai_agent_token_usage u
              WHERE u.tenant_id=%s::uuid AND u.run_id IN (SELECT extraction_id FROM saas_knowledge_campaign_fragments
              WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid)''',(tenant,tenant,row['id']))
            tokens=cur.fetchone()['tokens']
            cur.execute('''SELECT count(DISTINCT r->>'id') AS messages FROM saas_knowledge_extractions e,
              LATERAL jsonb_array_elements(e.source_refs) r WHERE e.tenant_id=%s::uuid AND e.status='completed'
              AND e.id IN (SELECT extraction_id FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid)''',(tenant,tenant,row['id']))
            messages=cur.fetchone()['messages']
            cur.execute('''SELECT count(*) FILTER(WHERE m->>'status'='processed') AS processed,
              count(*) FILTER(WHERE m->>'status'<>'processed') AS unavailable FROM
              (SELECT DISTINCT ON (m->>'channel_id',m->>'id') m FROM saas_knowledge_extractions e,
              LATERAL jsonb_array_elements(e.media_results) m WHERE e.tenant_id=%s::uuid
              AND e.id IN (SELECT extraction_id FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid)
              ORDER BY m->>'channel_id',m->>'id',e.created_at DESC) evidence''',(tenant,tenant,row['id']))
            media=cur.fetchone()
            cur.execute('''SELECT c.status,count(DISTINCT c.id) AS n FROM saas_knowledge_candidates c
              WHERE c.tenant_id=%s::uuid AND c.extraction_id IN (SELECT extraction_id FROM saas_knowledge_campaign_fragments
              WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid) GROUP BY c.status''',(tenant,tenant,row['id']))
            reviews={r['status']:r['n'] for r in cur.fetchall()}
            from tenant_knowledge_quality import schema as quality_schema
            quality_schema(cur)
            cur.execute('''SELECT retrieval_state,response_state,count(*) AS n FROM saas_knowledge_quality_checks
              WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid GROUP BY retrieval_state,response_state''',(tenant,row['id']))
            quality=cur.fetchall()
            cur.execute('''SELECT COALESCE(sum(total_tokens),0) AS tokens FROM saas_ai_test_turns
              WHERE tenant_id=%s::uuid AND id IN (SELECT id FROM saas_knowledge_quality_checks
              WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid)''',(tenant,tenant,row['id']))
            tokens=int(tokens)+int(cur.fetchone()['tokens'])
            grant_used=0;old_unknown=0;prior_settled=0;grant_status=None;grant_approved=False
            if row.get('max_cost_microusd'):
                from tenant_ai_spend_budget import ensure_schema as spend_schema
                spend_schema(conn)
                cur.execute('''SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_microusd
                   ELSE COALESCE(actual_microusd,reserved_microusd) END),0) AS used FROM saas_ai_spend_reservations
                   WHERE tenant_id=%s::uuid AND grant_id=%s::uuid''',(tenant,row['id']))
                grant_used=int(cur.fetchone()['used'])
                cur.execute("SELECT COALESCE(sum(reserved_microusd) FILTER(WHERE state IN ('unknown','reserved')),0) AS held,COALESCE(sum(actual_microusd) FILTER(WHERE state='settled'),0) AS settled FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND grant_id IS NULL",(tenant,))
                prior=cur.fetchone();old_unknown=int(prior['held']);prior_settled=int(prior['settled'])
                cur.execute('SELECT status,approved_at FROM saas_ai_media_grants WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid',(tenant,row['id']))
                grant=cur.fetchone();grant_status=grant['status'] if grant else 'missing'
                grant_approved=bool(grant and grant.get('approved_at'))
            analysis_model='';asr_model=''
            if row.get('max_cost_microusd'):
                from tenant_ai_agent_key_policy import platform_model,choose_api_credential
                from tenant_connect_voice import transcription_model
                from tenant_platform import _fernet
                cur.execute('''SELECT a.model,i.secrets,i.metadata FROM saas_ai_agents a LEFT JOIN saas_tenant_integrations i
                  ON i.tenant_id=a.tenant_id AND i.provider='chat_ai' WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid''',(tenant,row['analysis_agent_id']))
                agent=cur.fetchone() or {};metadata=agent.get('metadata') or {};company_key=''
                analysis_model=agent.get('model') or metadata.get('model') or platform_model()
                if agent.get('secrets'):
                    try:
                        secret=json.loads(_fernet().decrypt(bytes(agent['secrets'])))
                        if secret.get('tenant_id')==tenant and secret.get('provider')=='chat_ai':company_key=str(secret.get('api_key') or '')
                    except Exception:company_key=''
                _,credential_source=choose_api_credential(tenant,str(row['analysis_agent_id']),'connect',company_key,analysis_model)
                try:asr_model=transcription_model({'credential_source':credential_source,'config':metadata})
                except Exception:asr_model=''
            asr_tariff={}
            if asr_model:
                from tenant_ai_asr_model import tariff_status as asr_tariff_status
                asr_tariff=asr_tariff_status(asr_model)


    return {'campaign':{'id':str(row['id']),'version':row['version'],'status':row['status'],
      'channel_ids':row['sources'],'fragments':counts,'cards':cards,
      'reserved_calls':row['reserved_calls'],'reserved_tokens':row['reserved_tokens'],
      'media_policy':row['media_policy'],'reserved_kinds':row['reserved_kinds'],
      'used_tokens':int(tokens),'cost':None,'max_calls':row['max_calls'],'max_tokens':row['max_tokens'],
      'progress':progress,'media_processed':media['processed'],'media_unavailable':media['unavailable'],'processed_messages':messages,'reviews':reviews,'quality_checks':quality,
      'max_cost_microusd':row.get('max_cost_microusd'),'video_model':row.get('video_model'),'photo_model':row.get('photo_model'),
      'grant_used_microusd':grant_used,'prior_unknown_holds_microusd':old_unknown,
      'prior_settled_microusd':prior_settled,'grant_status':grant_status,
      'grant_approved':grant_approved,
      'analysis_model':analysis_model,'asr_model':asr_model,'asr_tariff':asr_tariff}}


def complete(claimed, response):
    """Fence completion by lease; partial media never becomes completed history."""
    request=response['request'];tenant=str(claimed['tenant_id'])
    media=request.get('media_results')
    missing=media is None or any(m.get('status')!='processed' or m.get('complete') is False or m.get('kind')=='video' or m.get('audio_status')=='unavailable_or_error' for m in media)
    state=request['status']
    if state=='completed' and missing:state='partial'
    if state not in {'completed','partial','failed','unknown'}:state='unknown'
    code=request.get('error_code') or ('media_incomplete' if missing else '')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_knowledge_campaign_fragments SET status=%s,extraction_id=%s::uuid,
              result=%s::jsonb,error_code=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid
              AND status IN ('running','unknown') AND lease_token=%s::uuid RETURNING id''',
              (state,request['id'],_json({'message_count':request.get('message_count'),'media_results':media}),
               code,tenant,claimed['id'],claimed['lease_token']))
            if not cur.fetchone():return False
            cur.execute('SELECT progress FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,claimed['campaign_id']))
            campaign=cur.fetchone()
            mode=(campaign or {}).get('progress',{}).get('mode')
            single_message=mode=='single_message'
            if single_message:
                # One exact owner consent must never grow into discovery,
                # follow-up pages or quality work.  Terminal result remains
                # stored on the fragment; the campaign itself is closed.
                cur.execute("UPDATE saas_knowledge_campaigns SET status='stopped',version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,claimed['campaign_id']))
            elif mode in {'case_media','case_media_batch'}:
                cur.execute("SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status IN ('pending','running','unknown') LIMIT 1",(tenant,claimed['campaign_id']))
                if not cur.fetchone():
                    cur.execute("UPDATE saas_knowledge_campaigns SET status='stopped',version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,claimed['campaign_id']))
                    if mode=='case_media_batch':
                        cur.execute("UPDATE saas_ai_media_grants SET status='completed' WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='active'",(tenant,claimed['campaign_id']))
            # Completed text can advance while unavailable attachments remain a
            # durable partial fragment for an explicit, budgeted later retry.
            if mode not in {'single_message','case_media','case_media_batch'} and request.get('next_before') and state in {'completed','partial'}:
                cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,claimed['campaign_id']))
                campaign=cur.fetchone()
                route={**claimed['route'],'before':identifier(request['next_before'])}
                if route['before']==claimed['route'].get('before'):raise TenantLinearError('Курсор не изменился.',409)
                enqueue(cur,claimed['profile'],campaign,route)
        conn.commit()
    return True


class CampaignStartReadbackFailed(Exception):
    """The approval transaction committed, but its result could not be read."""
    def __init__(self, error_class):
        super().__init__('campaign start committed; readback failed')
        self.error_class = error_class


def approve(profile,campaign_id,data):
    """Explicit source/budget confirmation, never called by deployment/startup."""
    required={'expected_version','channel_ids','max_calls','max_tokens','confirm_external_processing','media_policy'}
    valid_shapes=(required,required|{'case_id','case_plan_hash'},
      required|{'all_cases_snapshot_hash','max_cost_microusd','video_model'},
      required|{'all_cases_snapshot_hash','max_cost_microusd','video_model','acknowledge_prior_unknown'},
      required|{'all_cases_snapshot_hash','max_cost_microusd','video_model','photo_model'},
      required|{'all_cases_snapshot_hash','max_cost_microusd','video_model','photo_model','acknowledge_prior_unknown'})
    if not isinstance(data,dict) or set(data) not in valid_shapes or data['confirm_external_processing'] is not True:
        raise TenantLinearError('Подтвердите источники и бюджет внешней обработки.')
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,actor,access=store.authorize(cur,profile)
            cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,identifier(campaign_id)))
            row=cur.fetchone()
            if not row:raise TenantLinearError('Процесс не найден.',404)
            if row['status'] not in {'awaiting_budget','budget_exhausted'} or row['version']!=data['expected_version']:
                raise TenantLinearError('Обновите план перед подтверждением.',409)
            if not isinstance(data['channel_ids'],list) or row['sources']!=sorted(set(data['channel_ids'])):
                raise TenantLinearError('Источники изменились.',409)
            calls,tokens=data['max_calls'],data['max_tokens']
            if type(calls) is not int or not 2<=calls<=10000 or type(tokens) is not int or not 24000<=tokens<=160000000:
                raise TenantLinearError('Некорректный бюджет.')
            if not isinstance(data['media_policy'],dict) or set(data['media_policy'])!=FIELDS:raise TenantLinearError('Некорректные разрешения.')
            new_policy=validate(data['media_policy'],calls)
            if row['status']=='awaiting_budget' and new_policy!=row['media_policy']:raise TenantLinearError('Разрешения изменились.',409)
            if row['status']=='budget_exhausted':
                for flag in ('allow_external_media','allow_paid_asr'):
                    if new_policy[flag]!=row['media_policy'].get(flag,False):raise TenantLinearError('Подготовьте новую кампанию для изменения разрешений.',409)
                for kind in ('main','asr','vision'):
                    if new_policy['max_'+kind+'_calls']<row['media_policy'].get('max_'+kind+'_calls',0):raise TenantLinearError('Лимит нельзя уменьшить.',409)
            if row['status']=='awaiting_budget' and (row['max_calls']!=calls or row['max_tokens']!=tokens):
                raise TenantLinearError('Бюджет изменился.',409)
            if row['status']=='budget_exhausted' and (calls<row['max_calls'] or tokens<row['max_tokens'] or (calls==row['max_calls'] and tokens==row['max_tokens'] and new_policy==row['media_policy'])):
                raise TenantLinearError('Новый общий бюджет должен быть больше прежнего.',409)

            amount=reservation(new_policy)
            if not fits(new_policy,row['reserved_kinds'],amount) or calls<amount['calls'] or tokens<amount['tokens']:raise TenantLinearError('Бюджет не покрывает фрагмент.')
            case_mode=(row.get('progress') or {}).get('mode')=='case_media'
            batch_mode=(row.get('progress') or {}).get('mode')=='case_media_batch'
            if batch_mode and access.profile.get('role')!='owner':
                raise TenantLinearError('Only the company owner can approve the current-case media grant.',403)
            if batch_mode and row['status']=='budget_exhausted':
                raise TenantLinearError('The approved fixed $10 grant cannot be topped up or renewed.',409)
            case_plan=None;batch_plan=None;batch_routes=[]
            if case_mode:
                if (set(data)!=required|{'case_id','case_plan_hash'}
                    or data['case_id']!=row['progress']['case_id']
                    or data['case_plan_hash']!=row['progress']['plan_hash']):
                    raise TenantLinearError('Confirm this exact case and attachment plan.',409)
                from tenant_case_media_admission import plan, routes
                case_plan=plan(cur,profile,row['progress']['case_id'])
                if case_plan['status']!='pending_consent' or case_plan['plan_hash']!=row['progress']['plan_hash']:
                    raise TenantLinearError('Case boundary or attachment changed; prepare a new plan.',409)
                pending=list(routes(case_plan,row['analysis_agent_id']))
                if not pending:
                    raise TenantLinearError('Case media already complete.',409)
                if not new_policy['allow_external_media']:
                    raise TenantLinearError('External media consent required.',409)
                pending_ids={route['message_ids'][0] for route in pending}
                visual=any(item['kind'] in {'image','video','document'} for item in case_plan['media'] if item['id'] in pending_ids)
                audio=any(item['kind'] in {'audio','voice','video'} for item in case_plan['media'] if item['id'] in pending_ids)
                if (new_policy['max_main_calls']<len(pending)
                    or visual and not amount['vision']
                    or audio and not amount['asr']
                    or any(new_policy['max_'+kind+'_calls']<len(pending)*amount[kind]
                           for kind in ('main','asr','vision'))
                    or calls<len(pending)*amount['calls'] or tokens<len(pending)*amount['tokens']):
                    raise TenantLinearError('Budget does not cover all case attachments.',409)
            elif batch_mode:
                if set(data) not in (required|{'all_cases_snapshot_hash','max_cost_microusd','video_model'},
                    required|{'all_cases_snapshot_hash','max_cost_microusd','video_model','acknowledge_prior_unknown'},
                    required|{'all_cases_snapshot_hash','max_cost_microusd','video_model','photo_model'},
                    required|{'all_cases_snapshot_hash','max_cost_microusd','video_model','photo_model','acknowledge_prior_unknown'}):
                    raise TenantLinearError('Confirm this exact current-case snapshot and USD 10 limit.',409)
                retry_ack=bool(row['progress'].get('acknowledge_prior_unknown'))
                if (data.get('acknowledge_prior_unknown',False) is not retry_ack
                    or data['all_cases_snapshot_hash']!=row['progress'].get('snapshot_hash')
                    or data['max_cost_microusd']!=10_000_000
                    or data.get('video_model')!=row.get('video_model')
                    or ('photo_model' in data and data.get('photo_model')!=row.get('photo_model'))
                    or (row.get('photo_model') and data.get('photo_model')!=row.get('photo_model'))):
                    raise TenantLinearError('Confirm this exact current-case snapshot and USD 10 limit.',409)
                from tenant_case_media_admission import plan_existing_cases,existing_routes
                batch_plan=plan_existing_cases(cur,profile,acknowledge_prior_unknown=retry_ack)
                if batch_plan['snapshot_hash']!=row['progress']['snapshot_hash']:
                    raise TenantLinearError('Current case snapshot changed; prepare a new plan.',409)
                batch_routes=list(existing_routes(batch_plan,row['analysis_agent_id'],row.get('video_model'),row.get('photo_model')))
                if not batch_routes or not new_policy['allow_external_media']:
                    raise TenantLinearError('No eligible case attachments or media permission.',409)
                if row.get('photo_model') and _configured_photo_model(cur,tenant,row['analysis_agent_id'])!=row['photo_model']:
                    raise TenantLinearError('Company photo model changed; prepare a new snapshot.',409)
                pending_ids={route['message_ids'][0] for route in batch_routes}
                media_by_id={media['id']:media for case in batch_plan['cases'] for media in case.get('media',[])}
                visual=any(media_by_id[i]['kind'] in {'image','video','document'} for i in pending_ids)
                audio=any(media_by_id[i]['kind'] in {'audio','voice','video'} for i in pending_ids)
                count=len(batch_routes)
                if (new_policy['max_main_calls']<count or visual and not amount['vision']
                    or audio and not amount['asr']
                    or any(new_policy['max_'+kind+'_calls']<count*amount[kind] for kind in ('main','asr','vision'))
                    or calls<count*amount['calls'] or tokens<count*amount['tokens']):
                    raise TenantLinearError('Campaign call/token limits do not cover this snapshot.',409)
                if any(media_by_id[i]['kind']=='video' for i in pending_ids) and not row.get('video_model'):
                    raise TenantLinearError('A separate video model must be selected.',409)
                if any(media_by_id[i]['kind']=='video' for i in pending_ids):
                    from tenant_ai_video_model import require_video_tariff
                    require_video_tariff(row['video_model'])
                # Model and per-operation tariffs are immutable prerequisites;
                # unknown pricing blocks consent and no provider call starts.
                from tenant_ai_spend_budget import price,media_price
                asr_required=bool(audio and new_policy.get('allow_paid_asr') and new_policy.get('max_asr_calls',0)>0)
                agent_model,asr_model=_analysis_asr_model(cur,tenant,row['analysis_agent_id'],resolve_asr=asr_required)
                if not agent_model or not price(agent_model):
                    raise TenantLinearError('ai_spend_price_unknown',409)
                needs_base_vision=any(media_by_id[i]['kind']=='document' or
                    media_by_id[i]['kind']=='image' and not row.get('photo_model') for i in pending_ids)
                if needs_base_vision and media_price(agent_model,'vision_low') is None:
                    raise TenantLinearError(f'ai_spend_media_price_unknown: model={agent_model} operation=vision_low',409)
                if any(media_by_id[i]['kind']=='video' for i in pending_ids) and media_price(row.get('video_model') or '', 'vision_low') is None:
                    raise TenantLinearError(f'ai_spend_media_price_unknown: model={row.get("video_model") or ""} operation=vision_low',409)
                if asr_required:
                    from tenant_ai_asr_model import quote as quote_asr
                    quote_asr(asr_model)
            else:
                if set(data)!=required:raise TenantLinearError('Некорректные параметры.',400)
                from tenant_knowledge_campaign_discovery import seed
                if row['status']=='awaiting_budget':seed(cur,profile,row)
            cur.execute("UPDATE saas_knowledge_campaigns SET status='running',max_calls=%s,max_tokens=%s,media_policy=%s::jsonb,approved_by=%s,approved_at=now(),version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid",(calls,tokens,_json(new_policy),actor,tenant,campaign_id))
            if batch_mode:
                cur.execute('''UPDATE saas_ai_media_grants SET status='active',approved_by=%s,approved_at=now()
                  WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND id=%s::uuid
                    AND status='pending' AND cap_microusd=10000000 RETURNING id''',(actor,tenant,campaign_id,campaign_id))
                if not cur.fetchone():raise TenantLinearError('Media grant is no longer available.',409)
            if case_plan or batch_plan:
                approved={**row,'media_policy':new_policy}
                selected_routes=pending if case_plan else batch_routes
                for fragment_route in selected_routes:enqueue(cur,profile,approved,fragment_route)
            from tenant_knowledge_quality import schema as quality_schema
            quality_schema(cur)
            cur.execute('''UPDATE saas_knowledge_quality_checks q SET state='pending' WHERE tenant_id=%s::uuid
              AND campaign_id=%s::uuid AND state='blocked' AND NOT EXISTS(SELECT 1 FROM saas_ai_test_turns t WHERE t.tenant_id=q.tenant_id AND t.id=q.id)''',(tenant,campaign_id))
            store._audit(cur,tenant,actor,'knowledge_campaign_budget_approved','knowledge_campaign',campaign_id)
        conn.commit()
    try:
        return get(profile,campaign_id)
    except Exception as error:
        raise CampaignStartReadbackFailed(type(error).__name__) from None

def preview(profile,channels):
    if not isinstance(channels,list) or not 1<=len(channels)<=100:raise TenantLinearError('Выберите источники.')
    channels=sorted(set(identifier(c) for c in channels))
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,_,_=store.authorize(cur,profile)
            cur.execute('SELECT connect_channel_id,name FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=ANY(%s::uuid[])',(tenant,channels))
            sources=[{'channel_id':str(r['connect_channel_id']),'name':r['name']} for r in cur.fetchall()]
            if len(sources)!=len(channels):raise TenantLinearError('Источник недоступен.',404)
            cur.execute('''SELECT m.message_type,count(*) AS copies,min(COALESCE(m.happened_at,m.created_at)) AS earliest,
              max(COALESCE(m.happened_at,m.created_at)) AS latest FROM saas_crm_messages m
              JOIN saas_connect_conversations c ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id
              AND m.channel='connect:'||c.connect_channel_id::text WHERE c.tenant_id=%s::uuid
              AND c.connect_channel_id=ANY(%s::uuid[]) GROUP BY m.message_type''',(tenant,channels))
            types=[{'type':r['message_type'],'stored_copies':r['copies'],'earliest':str(r['earliest']),'latest':str(r['latest'])} for r in cur.fetchall()]
    return {'sources':sources,'message_types':types,'counts_include_channel_copies':True,
      'media_availability':'not_probed','currency_estimate':None,'calls_per_fragment_max':2,'reserved_tokens_per_fragment':24000,
      'messages_per_fragment_max':80,'warning':'Actual availability, unique group events and fragment counts require bounded discovery; no external media has been accessed.'}


def preview_existing_cases(profile, *, acknowledge_prior_unknown=False, analysis_agent_id=None):
    """Read-only owner preview for the exact bounded current-case media set."""
    with _connect() as conn:
        with conn.cursor() as cur:
            from tenant_case_media_admission import plan_existing_cases
            snapshot=plan_existing_cases(cur,profile,acknowledge_prior_unknown=acknowledge_prior_unknown)
            if analysis_agent_id is not None:
                tenant,_,_=store.authorize(cur,profile)
                agent_id=identifier(analysis_agent_id)
                cur.execute('SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL',(tenant,agent_id))
                if not cur.fetchone():
                    raise TenantLinearError('Analysis agent is unavailable.',404)
                _,asr_model=_analysis_asr_model(cur,tenant,agent_id)
                from tenant_ai_asr_model import tariff_status
                snapshot['asr_model']=asr_model
                snapshot['asr_tariff']=tariff_status(asr_model)
    return snapshot
