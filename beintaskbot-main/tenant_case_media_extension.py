"""Read-only preview for case sources under an existing fixed-budget grant.

This module does not add sources, reserve funds, or authorize provider dispatch.
"""
from tenant_linear_policy import TenantLinearError, identifier
import tenant_knowledge_candidates_store as cases
from tenant_case_source_inventory import inspect
from tenant_case_media_admission import _historical_attempt_reconciliation


def preview(cur,profile,campaign_id,expected_campaign_version,sources):
    tenant,actor,policy=cases.authorize(cur,profile)
    if policy.profile.get('role')!='owner':raise TenantLinearError('Owner access required.',403)
    campaign_id=identifier(campaign_id)
    if type(expected_campaign_version) is not int or expected_campaign_version<1 or not isinstance(sources,list) or not 1<=len(sources)<=25:
        raise TenantLinearError('Invalid extension preview.',400)
    cur.execute('''SELECT c.version,c.status,c.max_cost_microusd,c.progress,g.id AS grant_id,g.status AS grant_status,
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
      COALESCE(sum(reserved_microusd) FILTER(WHERE state IN ('reserved','unknown')),0) AS held
      FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND grant_id=%s::uuid''',(tenant,campaign_id))
    totals=cur.fetchone()
    cur.execute('''SELECT COALESCE(sum(reserved_microusd) FILTER(WHERE state IN ('reserved','unknown')),0) AS held
      FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND grant_id IS NULL''',(tenant,))
    prior_held=int(cur.fetchone()['held']);results=[];keys=set();snapshot=[]
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
        else:
            history=_historical_attempt_reconciliation(cur,tenant,message_id,source_data['source_hash'])
            state='known_not_attempted' if history.get('state')=='known_not_attempted' else 'previous_outcome_unknown'
        results.append({**item,'extension_state':state,'reconciliation':history})
        snapshot.append({'case_id':case_id,'message_id':message_id,'case_version':source['expected_version'],
          'channel_id':item['channel_id'],'lead_id':item['lead_id'],'kind':item['kind'],
          'source_version':source_data['source_version'],'source_hash':source_data['source_hash'],
          'state':state,'reconciliation':history})
    snapshot.sort(key=lambda x:(x['case_id'],x['message_id']))
    spent=int(totals['settled']);held=int(totals['held'])
    budget={'cap_microusd':int(campaign['cap_microusd']),'settled_microusd':spent,'held_microusd':held,
            'available_microusd':max(0,int(campaign['cap_microusd'])-spent-held),'prior_unknown_holds_microusd':prior_held}
    return {'campaign_id':campaign_id,'campaign_version':campaign['version'],'grant_status':campaign['grant_status'],
      'budget':budget,'sources':results,'snapshot_hash':cases.digest({'tenant_id':tenant,'actor_id':actor,'campaign_id':campaign_id,'campaign_version':campaign['version'],'budget':budget,'sources':snapshot}),
      'budget_basis':'current_snapshot_not_reservation','execution_authorized':False}
