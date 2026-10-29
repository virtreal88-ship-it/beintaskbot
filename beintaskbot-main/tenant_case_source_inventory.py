"""Read-only owner-scoped source attribution for one existing case."""
import re
from tenant_linear_policy import TenantLinearError, identifier
from tenant_ai_group_coordination import group_jid
import tenant_knowledge_candidates_store as cases
from tenant_media_evidence import media_version, source_hash, _body_hash
from tenant_case_media_admission import _has_current_media_version, _current_source_addressable


def inspect(cur, profile, case_id, message_id, expected_version, campaign_id, *, _internal=False):
    tenant,_,policy=cases.authorize(cur,profile)
    if policy.profile.get('role')!='owner':raise TenantLinearError('Owner access required.',403)
    case_id=identifier(case_id);message_id=identifier(message_id);campaign_id=identifier(campaign_id)
    if type(expected_version) is not int or expected_version<1:raise TenantLinearError('Invalid case version.',400)
    cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE',(tenant,case_id))
    case=cur.fetchone()
    if not case or case['status'] in {'archived','rejected'}:raise TenantLinearError('Case unavailable.',404)
    if case['version']!=expected_version:raise TenantLinearError('Case changed; refresh.',409)
    evidence=message_id in {str(v) for v in (case.get('content') or {}).get('source_message_ids',[])}
    cur.execute('''SELECT c.sources,c.progress,c.approved_at,g.id AS grant_id,g.approved_at AS grant_approved_at,g.status AS grant_status,g.cap_microusd
      FROM saas_knowledge_campaigns c LEFT JOIN saas_ai_media_grants g ON g.tenant_id=c.tenant_id AND g.campaign_id=c.id
      WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid FOR SHARE OF c''',(tenant,campaign_id))
    campaign=cur.fetchone()
    if not campaign:raise TenantLinearError('Campaign unavailable.',404)
    plan=next((p for p in (campaign.get('progress') or {}).get('case_plans',[]) if p.get('case_id')==case_id),{})
    media=next((m for m in plan.get('media',[]) if m.get('id')==message_id),{})
    approved_plan=bool(campaign.get('approved_at') and campaign.get('grant_approved_at') and str(campaign.get('grant_id') or '')==campaign_id
      and (campaign.get('progress') or {}).get('mode')=='case_media_batch' and campaign.get('grant_status') in {'active','completed'}
      and plan.get('case_version')==expected_version and media)
    if not evidence and not approved_plan:raise TenantLinearError('Message is not in this case or approved snapshot.',403)
    run=cases.extraction_row(cur,tenant,case['extraction_id']);route=run.get('route') or {}
    conversation=str(route.get('conversation_id') or '');case_channel=identifier(route.get('channel_id'))
    cases.assert_route(cur,tenant,policy,'connect',conversation,case_channel)
    refs=[r for r in (run.get('source_refs') or []) if isinstance(r,dict) and str(r.get('id'))==message_id]
    if len(refs)>1 or (evidence and len(refs)!=1):raise TenantLinearError('Case source attribution is unavailable.',409)
    ref=refs[0] if refs else None;channel=identifier(ref.get('channel_id') if ref else plan.get('channel_id'))
    if channel!=case_channel and not group_jid(conversation):raise TenantLinearError('Source is outside the case route.',403)
    selected=cases.assert_route(cur,tenant,policy,'connect',conversation,channel)
    cur.execute('''SELECT id::text AS id,external_id,message_type,channel,raw,body,COALESCE(happened_at,created_at) AS occurred_at FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND kommo_lead_id=%s AND channel=%s FOR SHARE''',
      (tenant,message_id,selected['lead_id'],'connect:'+channel))
    row=cur.fetchone()
    if not row:raise TenantLinearError('Source message is unavailable in its attributed channel.',404)
    raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
    if raw.get('conversationId') and raw['conversationId']!=conversation:raise TenantLinearError('Source is outside the case route.',403)
    # Legacy source references recorded the internal message UUID and channel
    # without external_id/media_kind. The exact tenant/channel/conversation,
    # case membership and message UUID were checked above. Missing old fields
    # are not evidence of a changed source; fields that were actually saved
    # still have to match exactly. This does not establish historical bytes.
    legacy_ref=bool(ref and ('external_id' not in ref or 'media_kind' not in ref))
    if ref and (('external_id' in ref and str(row.get('external_id') or '')!=str(ref.get('external_id') or ''))
                or ('media_kind' in ref and str(row.get('message_type') or '')!=str(ref.get('media_kind') or ''))):
        raise TenantLinearError('Case source attribution changed.',409)
    if not row.get('external_id'):raise TenantLinearError('Case source address unavailable.',409)
    if row['message_type'] not in {'image','audio','voice','video','document'}:raise TenantLinearError('Case source is not a supported attachment.',409)
    version=media_version(raw);ref_hash=source_hash({'external_id':row['external_id'],'body_hash':_body_hash(row.get('body')),'media_kind':row['message_type'],'media_version':version})
    cur.execute("SELECT to_regclass('public.saas_media_evidence_receipts') AS relation")
    cached=None
    if cur.fetchone().get('relation'):
        cur.execute('SELECT status,code FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND external_id=%s AND source_hash=%s AND media_kind=%s',
          (tenant,channel,row['external_id'],ref_hash,row['message_type']))
        cached=cur.fetchone()
    covered=bool(approved_plan
      and channel in [str(c) for c in campaign.get('sources') or []] and str(plan.get('channel_id') or '')==channel
      and plan.get('case_version')==expected_version and media.get('source_version')==version
      and media.get('source_hash')==ref_hash)
    if not evidence and not covered:raise TenantLinearError('Source is not in this approved snapshot.',403)
    cached_code=str(cached.get('code') or '') if cached else ''
    if cached_code and not re.fullmatch(r'[a-z_]{1,80}',cached_code):cached_code='other'
    result={'case_id':case_id,'case_version':expected_version,'message_id':message_id,'channel_id':channel,
      'lead_id':selected['lead_id'],'kind':row['message_type'],'legacy_source_reference':legacy_ref,'source_version_verified':_has_current_media_version(raw),
      'current_source_addressable':_current_source_addressable(row,channel,conversation),
      'cached_status':str(cached['status']) if cached else None,'cached_code':cached_code if cached else None,
      'current_bytes_checked':False,'grant_scope_match':covered,'grant_status':campaign.get('grant_status'),
      'grant_cap_microusd':campaign.get('cap_microusd'),'dispatch_authorized':False}
    if _internal:result['_source']={'external_id':row['external_id'],'conversation_id':conversation,'source_hash':ref_hash,'source_version':version,'occurred_at':str(row['occurred_at'])}
    return result


def verify(profile,case_id,message_id,expected_version,campaign_id,*,include_content=False):
    """Fetch one grant-covered source and compare actual bytes with saved vision results.

    This read never calls an AI provider, changes a grant, or releases a hold.
    """
    import asyncio,hashlib
    from tenant_platform import _connect
    from tenant_chat_attachment_store import load
    from tenant_chat_attachment_api import admit
    from tenant_connect_media import download_media
    from tenant_media_evidence import _safe_text
    with _connect() as conn:
        with conn.cursor() as cur:before=inspect(cur,profile,case_id,message_id,expected_version,campaign_id,_internal=True)
    if not before['grant_scope_match']:
        raise TenantLinearError('Source is not covered by this approved campaign snapshot.',403)
    source=before['_source'];admit(profile)
    kwargs={'channel_id':before['channel_id'],'conversation_id':source['conversation_id']}
    item=load(profile,before['lead_id'],source['external_id'],**kwargs)
    if str((item.get('row') or {}).get('id'))!=before['message_id']:
        raise TenantLinearError('Source changed; refresh.',409)
    data,mime=asyncio.run(download_media(item))
    current=load(profile,before['lead_id'],source['external_id'],**kwargs)
    if current!=item:raise TenantLinearError('Source changed; refresh.',409)
    with _connect() as conn:
        with conn.cursor() as cur:
            after=inspect(cur,profile,case_id,message_id,expected_version,campaign_id,_internal=True)
            if after!=before or not after['grant_scope_match']:raise TenantLinearError('Case, source or grant changed; refresh.',409)
            results=[];transcripts=[]
            cur.execute("SELECT to_regclass('public.saas_knowledge_media_cache') AS relation")
            cache_exists=bool((cur.fetchone() or {}).get('relation'))
            if cache_exists and before['kind']=='image':
                digest=hashlib.sha256(data).hexdigest()
                identity={'message_id':before['message_id'],'external_id':source['external_id'],'channel_id':before['channel_id']}
                cur.execute('''SELECT transcript FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid
                  AND state='completed' AND metadata->>'owner_retry_grant'=%s AND metadata->>'kind'='vision'
                  AND metadata->>'sha256'=%s AND metadata->>'mime'=%s AND metadata->'owner_retry_source'=%s::jsonb
                  ORDER BY created_at DESC LIMIT 2''',(str(profile['tenant_id']),campaign_id,digest,mime,__import__('json').dumps(identity)))
                results=[_safe_text(row['transcript']) for row in cur.fetchall() if row.get('transcript')]
            elif cache_exists and before['kind'] in {'audio','voice'} and mime!='audio/aac':
                digest=hashlib.sha256(data).hexdigest()
                identity={'message_id':before['message_id'],'external_id':source['external_id'],'channel_id':before['channel_id']}
                cur.execute('''SELECT s.transcript,s.metadata->>'artifact_fingerprint' AS fingerprint,a.metadata->>'model' AS model
                  FROM saas_knowledge_media_cache s JOIN saas_knowledge_media_cache a ON a.tenant_id=s.tenant_id
                    AND a.state='completed' AND a.metadata->>'owner_retry_component'='asr'
                    AND a.metadata->>'artifact_fingerprint'=s.metadata->>'artifact_fingerprint'
                    AND a.metadata->>'owner_retry_artifact'='true'
                  WHERE s.tenant_id=%s::uuid AND s.state='completed' AND s.metadata->>'owner_retry_grant'=%s
                    AND s.metadata->>'owner_retry_component'='asr' AND s.metadata->'owner_retry_source'=%s::jsonb
                  ORDER BY s.created_at DESC LIMIT 2''',(str(profile['tenant_id']),campaign_id,__import__('json').dumps(identity)))
                for row in cur.fetchall():
                    expected=cases.digest({'sha256':digest,'model':row.get('model'),'format':mime})
                    if expected==row.get('fingerprint') and row.get('transcript'):transcripts.append(_safe_text(row['transcript']))
    before.pop('_source');before['current_bytes_checked']=True;before['source_available']=True
    before['verified_vision_result_count']=len(results)
    before['verified_asr_result_count']=len(transcripts)
    if include_content:before.update(verified_vision_observations=results,verified_asr_transcripts=transcripts)
    return before
