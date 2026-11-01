"""Canonical-owner, exact-dialog metadata only; never prepares or retries AI."""
import re
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError,identifier
from tenant_operator_access import access,assert_route

STATES={'sending','completed','unknown','skipped'}
STAGES={'source_validation','media_download','media_validation','audio_transcription','visual_analysis','video_decode','visual_input_prepare','context_preparation','unknown'}
CODES={'media_unavailable','source_fetch_unavailable','source_check_failed','source_rate_limited','source_access_denied','source_changed','media_processing_failed','media_result_unknown','asr_authorization_required','legacy_voice_version_unverified','pre_dispatch_retryable','media_size_limit','asr_model_unavailable','transcript_truncated','audio_context_incomplete','image_context_incomplete','visual_budget','vision_not_approved','external_media_permission_required','model_has_no_vision','image_evidence_incomplete','audio_evidence_incomplete'}


def _source(value):
    return value if isinstance(value,str) and re.fullmatch(r'(?:connect:)?[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',value) else None


def _hash(value):
    return value if isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) else None


def read(profile,args):
    allowed={'channel_id','lead_id','request_id','limit','expected_tenant_id','expected_user_id'}
    if not isinstance(args,dict) or set(args)-allowed or not {'channel_id','lead_id'}<=set(args):
        raise TenantLinearError('Exact channel and lead required.',400)
    channel=identifier(args['channel_id'])
    if not isinstance(args['lead_id'],str) or not re.fullmatch(r'[1-9][0-9]{0,18}',args['lead_id']):
        raise TenantLinearError('Invalid lead.',400)
    lead=int(args['lead_id'])
    raw=args.get('limit','10')
    if lead>=2**63 or not isinstance(raw,str) or not re.fullmatch(r'[1-9]|10',raw):
        raise TenantLinearError('Invalid diagnostic limit.',400)
    limit=int(raw);request=identifier(args['request_id']) if 'request_id' in args else None
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,actor,policy=access(cur,profile,'dialogs')
            cur.execute("SET LOCAL statement_timeout='5000ms'")
            cur.execute('SELECT owner_telegram_id FROM saas_tenants WHERE id=%s::uuid',(tenant,))
            owner=cur.fetchone() or {}
            if policy.profile.get('role')!='owner' or owner.get('owner_telegram_id')!=actor:
                raise TenantLinearError('Current canonical company owner required.',403)
            chosen=assert_route(cur,tenant,policy,'connect',channel=channel,lead_id=lead)
            from tenant_operator_dialogs import projection
            from tenant_ai_agent_runtime_flags import enabled
            sql,params=projection(policy)
            cur.execute(sql+"SELECT 1 AS visible FROM visible WHERE provider='connect' AND channel_id=%s AND conversation_id=%s AND lead_id=%s AND channel_active=TRUE",
              (tenant,tenant,*params,enabled('AI_AGENTS_ENABLED'),channel,chosen['conversation_id'],lead))
            if not cur.fetchone():raise TenantLinearError('Dialog unavailable.',404)
            cur.execute("SELECT to_regclass('public.saas_chat_ai_runs') AS relation")
            if not (cur.fetchone() or {}).get('relation'):
                return {'coverage':'manual_runs_metadata_only','available':False,'items':[],'limited':False}
            cur.execute('''SELECT request_id,status,created_at,updated_at,
              result->'media_failure'->>'source_id' AS failure_source_id,
              result->'media_failure'->>'source_hash' AS failure_source_hash,
              result->'media_failure'->>'kind' AS failure_kind,
              result->'media_failure'->>'status' AS failure_status,
              result->'media_failure'->>'code' AS failure_code,
              result->'media_failure'->>'failure_stage' AS failure_stage,
              (SELECT jsonb_agg(jsonb_build_object('source_id',s->>'external_id','media_version',s->>'media_version','body_hash',s->>'body_hash','kind',s->>'media_kind'))
                FROM (SELECT value AS s FROM jsonb_array_elements(CASE WHEN jsonb_typeof(result->'source_versions')='array' THEN result->'source_versions' ELSE '[]'::jsonb END) LIMIT 20) AS sources) AS source_versions
              FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND provider='connect'
                AND channel_id=%s AND conversation_id=%s AND lead_id=%s
                AND (%s::uuid IS NULL OR request_id=%s::uuid)
              ORDER BY created_at DESC,request_id DESC LIMIT %s''',
              (tenant,channel,chosen['conversation_id'],lead,request,request,limit+1))
            rows=cur.fetchall()
    from tenant_media_admission import REASONS
    from tenant_chat_ai_provider import is_safe_failure_code
    items=[]
    for row in rows[:limit]:
        sources=[]
        for source in row.get('source_versions') or []:
            if isinstance(source,dict) and _source(source.get('source_id')):
                sources.append({'source_id':_source(source['source_id']),'media_version':_hash(source.get('media_version')),'body_hash':_hash(source.get('body_hash')),'kind':source.get('kind') if source.get('kind') in {'text','voice','audio','image','video','document'} else None})
        failure=None
        if row.get('failure_code') is not None:
            code=row['failure_code']
            failure={'source_id':_source(row.get('failure_source_id')),'source_hash':_hash(row.get('failure_source_hash')),
              'kind':row.get('failure_kind') if row.get('failure_kind') in {'voice','audio','image','video','document'} else None,
              'status':row.get('failure_status') if row.get('failure_status') in {'processed','partial','unavailable','unsupported','error','unknown','pending_processing','context_limit'} else None,
              'code':code if code in CODES or code in REASONS or is_safe_failure_code(code) else None,
              'failure_stage':row.get('failure_stage') if row.get('failure_stage') in STAGES else None}
        items.append({'request_id':str(row['request_id']),'status':row['status'] if row['status'] in STATES else None,
          'created_at':row['created_at'].isoformat() if hasattr(row['created_at'],'isoformat') else row['created_at'],
          'updated_at':row['updated_at'].isoformat() if hasattr(row['updated_at'],'isoformat') else row['updated_at'],
          'source_versions':sources,'media_failure':failure,'dispatch_proof':None})
    return {'coverage':'manual_runs_metadata_only','available':True,'items':items,'limited':len(rows)>limit,
      'limitations':['missing_reason_not_recoverable','dispatch_not_inferred','no_provider_or_media_probe']}
