"""One owner-selected current-image retry; old outcomes and holds remain intact."""
import json
import hashlib
import tenant_knowledge_candidates_store as cases


# This known unresolved video was explicitly excluded by the owner's consent.
_EXCLUDED_UNKNOWN_VIDEO_HASH='91b421cec813327b027f1eb1ccb18a5114ef84d7c07ef062428633763826b7ea'


def _excluded(message_id):
    return hashlib.sha256(str(message_id).encode()).hexdigest()==_EXCLUDED_UNKNOWN_VIDEO_HASH


def eligible(cur, tenant, source, sha256, *, exclude_fragment=None):
    if _excluded(source.get('message_id')) or source.get('kind') not in {'image','audio','voice'} or not isinstance(sha256, str) or len(sha256) != 64:
        return False
    message = str(source['message_id'])
    # Neither a live extraction nor a reserved call may race this attempt.
    cur.execute("""SELECT 1 FROM saas_knowledge_extractions e
      WHERE e.tenant_id=%s::uuid AND (e.source_refs @> %s::jsonb OR e.media_results @> %s::jsonb)
        AND (%s::uuid IS NULL OR e.id<>%s::uuid) AND e.status='running' LIMIT 1""",
      (tenant, json.dumps([{'id': message}]), json.dumps([{'id': message}]), exclude_fragment, exclude_fragment))
    if cur.fetchone(): return False
    # A completed exact-byte receipt must be shown/reused, never redispatched.
    cur.execute("""SELECT 1 FROM saas_knowledge_extractions e
      WHERE e.tenant_id=%s::uuid AND e.status='completed'
        AND (%s::uuid IS NULL OR e.id<>%s::uuid)
        AND e.media_results @> %s::jsonb
        AND EXISTS (SELECT 1 FROM jsonb_array_elements(e.media_results) r
          WHERE r->>'id'=%s AND r->>'sha256'=%s AND r->>'status' IN ('processed','partial')) LIMIT 1""",
      (tenant,exclude_fragment,exclude_fragment,json.dumps([{'id':message,'sha256':sha256}]),message,sha256))
    if cur.fetchone(): return False
    cur.execute("""SELECT 1 FROM saas_knowledge_campaign_fragments f
      WHERE f.tenant_id=%s::uuid AND f.route->'message_ids' @> %s::jsonb
        AND (%s::uuid IS NULL OR f.id<>%s::uuid) AND f.status IN ('running','pending') LIMIT 1""",
      (tenant, json.dumps([message]), exclude_fragment, exclude_fragment))
    if cur.fetchone(): return False
    # The separate Connect voice worker can process the same source outside
    # case campaigns. Its active receipt is a mandatory single-flight fence.
    if source.get('kind') in {'audio','voice'}:
        cur.execute("""SELECT 1 FROM saas_connect_voice_receipts v JOIN saas_crm_messages m
          ON m.tenant_id=v.tenant_id AND m.channel='connect:'||v.channel_id::text AND m.external_id=v.external_id
          WHERE m.tenant_id=%s::uuid AND m.id=%s::uuid AND v.state='processing' LIMIT 1""",(tenant,message))
        if cur.fetchone(): return False
    cur.execute("SELECT to_regclass('public.saas_ai_spend_reservations') AS relation")
    if not cur.fetchone().get('relation'): return False
    cur.execute("""SELECT 1 FROM saas_ai_spend_reservations s JOIN saas_knowledge_extractions e
      ON e.tenant_id=s.tenant_id AND s.call_key IN
        ('extraction:'||e.id::text, 'extraction:'||e.id::text||':vision', 'extraction:'||e.id::text||':photo', 'extraction:'||e.id::text||':asr')
      WHERE e.tenant_id=%s::uuid AND (e.source_refs @> %s::jsonb OR e.media_results @> %s::jsonb)
        AND (%s::uuid IS NULL OR e.id<>%s::uuid) AND s.state='reserved' LIMIT 1""",
      (tenant,json.dumps([{'id':message}]),json.dumps([{'id':message}]),exclude_fragment,exclude_fragment))
    if cur.fetchone(): return False
    cur.execute("SELECT to_regclass('public.saas_knowledge_media_cache') AS relation")
    if not cur.fetchone().get('relation'): return False
    # Ignore only the cache claim owned by this same fragment. Other live
    # claims remain a fence even if their extraction has already timed out.
    cur.execute("""SELECT 1 FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid AND state='running'
      AND (metadata->>'sha256'=%s OR metadata->'owner_retry_source'->>'message_id'=%s)
      AND (%s::text IS NULL OR metadata->>'owner_retry_extraction_id' IS DISTINCT FROM %s::text) LIMIT 1""",
      (tenant,sha256,message,exclude_fragment,exclude_fragment))
    if cur.fetchone(): return False
    if source.get('kind') in {'audio','voice'}:
        # Legacy ASR cache entries can lack any source metadata. A running
        # unattributed claim must finish before a new voice attempt starts.
        cur.execute("""SELECT 1 FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid AND state='running'
          AND COALESCE(metadata->>'kind','') IN ('','asr')
          AND (%s::text IS NULL OR metadata->>'owner_retry_extraction_id' IS DISTINCT FROM %s::text) LIMIT 1""",
          (tenant,exclude_fragment,exclude_fragment))
        if cur.fetchone():return False
    return True


def authorized(progress, campaign_id, fragment_id, route):
    extension = ((progress or {}).get('case_extensions') or {}).get(str(route.get('case_extension_id') or '')) or {}
    saved = (extension.get('sources') or {}).get(str(fragment_id)) or {}
    source = saved.get('source') or {}
    signature = cases.digest({'campaign_id':str(campaign_id),'request_id':str(route.get('case_extension_id') or ''),'source':source})
    return (extension.get('acknowledge_unknown_selected') is True and source.get('state')=='owner_uncertain_retry'
        and source.get('kind') in {'image','audio','voice'} and not _excluded(source.get('message_id')) and saved.get('signature')==signature==route.get('case_plan_hash')
        and route.get('message_ids')==[source.get('message_id')] and route.get('case_id')==source.get('case_id')
        and route.get('case_source_sha256')==source.get('current_sha256') and route.get('owner_retry_unknown') is True
        and str(route.get('media_grant_id'))==str(campaign_id))
