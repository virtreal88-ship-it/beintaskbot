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



def _source_receipts(cur,tenant,channel,lead,row):
    result={'status':'unknown','reason':'source_unverified','flight_table_available':None,'receipt_table_available':None,
      'flight':None,'receipt':None,'limitations':['source_records_not_request_linked','absence_not_dispatch_proof']}
    source=_source(row.get('failure_source_id'));version=_hash(row.get('failure_source_hash'))
    kind=row.get('failure_kind')
    if not source or not version or kind not in {'voice','audio','image','video','document'}:return result
    cur.execute('''SELECT 1 AS visible FROM saas_crm_messages WHERE tenant_id=%s::uuid
      AND channel=%s AND kommo_lead_id=%s AND external_id=%s AND message_type=%s LIMIT 1''',
      (tenant,'connect:'+channel,lead,source,kind))
    if not cur.fetchone():return result
    cur.execute("SELECT to_regclass('public.saas_episode_media_flights') AS flight, to_regclass('public.saas_media_evidence_receipts') AS receipt")
    tables=cur.fetchone() or {}
    result.update(reason='records_absent' if tables.get('flight') and tables.get('receipt') else 'records_unavailable',
      flight_table_available=bool(tables.get('flight')),receipt_table_available=bool(tables.get('receipt')))
    key=(tenant,channel,source,version)
    if tables.get('flight'):
        cur.execute('''SELECT state,created_at,result->>'status' AS result_status,result->>'code' AS code,
          result->>'failure_stage' AS failure_stage,result->>'failure_code' AS failure_code
          FROM saas_episode_media_flights WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
            AND external_id=%s AND source_hash=%s LIMIT 1''',key)
        saved=cur.fetchone()
        if saved:
            result['flight']=_lower_metadata(saved)
            result['flight']['state']=saved['state'] if saved['state'] in {'running','completed','unknown'} else None
    if tables.get('receipt'):
        cur.execute('''SELECT status AS result_status,code,created_at,updated_at,
          result->>'failure_stage' AS failure_stage,result->>'failure_code' AS failure_code,
          coverage->'complete' AS coverage_complete,provenance->>'processor' AS processor
          FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
            AND external_id=%s AND source_hash=%s AND media_kind=%s LIMIT 1''',(*key,kind))
        saved=cur.fetchone()
        if saved:
            result['receipt']=_lower_metadata(saved)
            result['receipt'].update(coverage_complete=saved.get('coverage_complete') if type(saved.get('coverage_complete')) is bool else None,
              processor=saved.get('processor') if saved.get('processor') in {'knowledge_extraction','conversation_episode'} else None)
    if result['flight'] is not None or result['receipt'] is not None:result.update(status='records_present',reason=None)
    return result


def _lower_metadata(row):
    from tenant_chat_ai_provider import is_safe_failure_code
    from tenant_media_admission import REASONS
    code=row.get('code')
    codes=CODES|set(REASONS)|{'vision_observation','legacy_exact_transcript','transcribed','document_text','text_extracted','awaiting_model','video_frames_sampled'}
    result={'status':row.get('result_status') if row.get('result_status') in {'processed','partial','unavailable','unsupported','error','unknown','pending_processing'} else None,
      'code':code if isinstance(code,str) and (code in codes or is_safe_failure_code(code)) else None,
      'failure_stage':row.get('failure_stage') if row.get('failure_stage') in STAGES else None,
      'failure_code':row.get('failure_code') if is_safe_failure_code(row.get('failure_code')) else None}
    for key in ('created_at','updated_at'):
        if key in row:result[key]=row[key].isoformat() if hasattr(row[key],'isoformat') else row[key]
    return result

def read(profile,args):
    allowed={'channel_id','lead_id','request_id','limit','expected_tenant_id','expected_user_id','include_source_receipts'}
    if not isinstance(args,dict) or set(args)-allowed or not {'channel_id','lead_id'}<=set(args):
        raise TenantLinearError('Exact channel and lead required.',400)
    include=args.get('include_source_receipts')=='1'
    if 'include_source_receipts' in args and (not include or not args.get('request_id')):
        raise TenantLinearError('Source receipt inspection requires one request UUID.',400)
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
            if include:
                for row in rows:row['_source_receipts']=_source_receipts(cur,tenant,channel,lead,row)
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
        if include:items[-1]['source_receipts']=row['_source_receipts']
    return {'coverage':'manual_runs_metadata_only','available':True,'items':items,'limited':len(rows)>limit,
      'limitations':['missing_reason_not_recoverable','dispatch_not_inferred','no_provider_or_media_probe']}
