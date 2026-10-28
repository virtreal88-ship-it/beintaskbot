"""Proofs for a failed media attempt; never changes a campaign or calls a provider."""

def no_media_dispatch_proven(fragment, extraction, reservations):
    """Fail closed unless the exact partial extraction released its media holds.

    Absence of a reservation is not evidence of an unbilled attempt. Successful,
    partial or uncertain outcomes must not become eligible for this recovery.
    """
    if not isinstance(fragment,dict) or not isinstance(extraction,dict):
        return False
    if fragment.get('status')!='partial' or extraction.get('status')!='completed':
        return False
    extraction_id=str(extraction.get('id') or '')
    if not extraction_id or str(fragment.get('extraction_id') or '')!=extraction_id:
        return False
    route=fragment.get('route') or {}
    ids=route.get('message_ids')
    results=extraction.get('media_results')
    if not isinstance(ids,list) or len(ids)!=1 or not isinstance(results,list) or len(results)!=1:
        return False
    result=results[0]
    if (not isinstance(result,dict) or str(result.get('id'))!=str(ids[0])
        or result.get('status')!='error' or result.get('code')!='media_unavailable'
        or result.get('failure_code')!='media_processing_failed'):
        return False
    kind=result.get('kind')
    stage=result.get('failure_stage')
    expected={'image':({'vision'},{'visual_analysis'}),
              'audio':({'asr'},{'media_validation','audio_transcription'}),
              'voice':({'asr'},{'media_validation','audio_transcription'}),
              'video':({'asr','vision'},{'visual_analysis'})}.get(kind)
    if not expected or stage not in expected[1]:
        return False
    if kind=='video' and result.get('audio_status')!='unavailable_or_error':
        return False
    relevant={}
    prefix='extraction:'+extraction_id+':'
    for row in reservations:
        if not isinstance(row,dict): return False
        key=str(row.get('call_key') or '')
        if not key.startswith(prefix): continue
        suffix=key[len(prefix):]
        if suffix in {'asr','vision','photo'}:
            if suffix in relevant:return False
            relevant[suffix]=row
    required=expected[0]
    if kind=='image' and 'photo' in relevant:
        required={'photo'}
    if not required.issubset(relevant):return False
    for row in relevant.values():
        if row.get('state')!='released' or row.get('actual_microusd')!=0:return False
    return True


def approved_parent(cur, tenant, campaign_id, parent_id):
    """Check server-owned evidence and grant binding at the dispatch fence."""
    cur.execute('''SELECT f.* FROM saas_knowledge_campaign_fragments f
      WHERE f.tenant_id=%s::uuid AND f.campaign_id=%s::uuid AND f.id=%s::uuid''',
      (tenant,campaign_id,parent_id))
    fragment=cur.fetchone()
    if not fragment or not fragment.get('extraction_id'):return None
    extraction_id=str(fragment['extraction_id'])
    cur.execute('''SELECT id,status,media_results FROM saas_knowledge_extractions
      WHERE tenant_id=%s::uuid AND id=%s::uuid''',(tenant,extraction_id))
    extraction=cur.fetchone()
    cur.execute('''SELECT call_key,state,actual_microusd,campaign_id,grant_id
      FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key=ANY(%s::text[])''',
      (tenant,['extraction:'+extraction_id+':'+suffix for suffix in ('asr','vision','photo')]))
    rows=cur.fetchall()
    if any(str(row.get('campaign_id') or '')!=str(campaign_id)
           or str(row.get('grant_id') or '')!=str(campaign_id) for row in rows):return None
    return fragment if no_media_dispatch_proven(fragment,extraction,rows) else None


def verified_recovery_parent(cur,tenant,campaign_id,progress,fragment_id,route):
    recovery=(progress or {}).get('failed_media_recovery') or {}
    parent_id=(recovery.get('parents') or {}).get(str(fragment_id))
    if not parent_id or not isinstance(route,dict):return None
    parent=approved_parent(cur,tenant,campaign_id,parent_id)
    if not parent or str(route.get('retry_of') or '')!=str(parent.get('extraction_id') or ''):
        return None
    if {k:v for k,v in route.items() if k!='retry_of'}!=parent.get('route'):
        return None
    return parent
