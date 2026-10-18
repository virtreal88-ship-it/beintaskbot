"""Bounded lesson documents and existing Knowledge Sources reuse; no sends or browser runs."""
from copy import deepcopy
import json,re,math
from tenant_linear_policy import TenantLinearError,identifier

OPERATION='inventory.warehouse_transfer'

def validate(value):
    if not isinstance(value,dict) or value.get('schema_version')!=1 or value.get('operation')!=OPERATION: raise ValueError('invalid_lesson')
    if value.get('language') not in {'az','ru','en'} or value.get('review_status') not in {'pending_review','approved','rejected'}: raise ValueError('invalid_review')
    if len(json.dumps(value,ensure_ascii=False).encode())>100000: raise ValueError('lesson_too_large')
    verification=value.get('verification',{})
    if verification.get('status')!='form_preparation_only' or verification.get('environment')!='Bein Demo': raise ValueError('unverified_operation')
    slides=value.get('slides');seen=set();total=0
    if not isinstance(slides,list) or len(slides)!=8: raise ValueError('invalid_slides')
    for i,s in enumerate(slides,1):
        if s.get('order')!=i or not isinstance(s.get('id'),str) or s['id'] in seen: raise ValueError('invalid_order')
        seen.add(s['id'])
        for key in ('title','explanation','note'):
            if not isinstance(s.get(key),str) or len(s[key])>2000: raise ValueError('invalid_text')
        image=s['image'];w=image['width'];h=image['height']
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.jpg',image['asset_id']) or not re.fullmatch('[0-9a-f]{64}',image['sha256']): raise ValueError('invalid_asset')
        if any(type(n) is not int or not 1<=n<=4096 for n in (w,h)): raise ValueError('invalid_dimensions')
        areas=s['areas'];ids=set()
        if not isinstance(areas,list) or len(areas)>8: raise ValueError('invalid_areas')
        for a in areas:
            if a['id'] in ids or a['intent'] not in {'focus','warning'}: raise ValueError('invalid_area')
            ids.add(a['id']);x,y,aw,ah=(a[k] for k in ('x','y','width','height'))
            if any(type(n) not in (int,float) or not math.isfinite(n) for n in (x,y,aw,ah)) or min(x,y)<0 or min(aw,ah)<=0 or x+aw>w or y+ah>h: raise ValueError('out_of_bounds')
        play=s['playback'];duration=play['duration_ms'];total+=duration
        if type(duration) is not int or not 4000<=duration<=30000 or type(play['transition_ms']) is not int or not 0<=play['transition_ms']<=1000: raise ValueError('invalid_duration')
        if type(play['return_to_overview']) is not bool or len(play['focus'])>4: raise ValueError('invalid_focus')
        previous=-1
        for f in play['focus']:
            if f['area_id'] not in ids or type(f['at_ms']) is not int or not previous<f['at_ms']<=duration-2000 or type(f['padding']) is not int or not 10<=f['padding']<=200: raise ValueError('invalid_focus')
            if 'only_below_scale' in f and (type(f['only_below_scale']) not in (int,float) or not 0<f['only_below_scale']<1): raise ValueError('invalid_focus_threshold')
            previous=f['at_ms']
    if total>144000: raise ValueError('lesson_duration_limit')
    return deepcopy(value)


def approved_lesson(cur,tenant_id,agent_id,*,language='az',ui_version='unreported'):
    """Use ONLY already employee-approved, active, agent-scoped Knowledge Sources.

    Caller must already hold live membership/agent authority. No publication function
    or new storage is introduced. Unreported UI versions require human revalidation;
    no automated browser fallback is authorized by this lookup.
    """
    tenant=identifier(tenant_id);agent=identifier(agent_id)
    if language not in {'ru','az','en'}: raise ValueError('invalid_language')
    if not ui_version or ui_version=='unreported': return None
    cur.execute("""SELECT k.id,k.content,k.provenance FROM saas_ai_knowledge_sources k
      JOIN saas_ai_agents a ON a.tenant_id=k.tenant_id AND a.id=k.agent_id
      JOIN saas_tenants t ON t.id=k.tenant_id
      WHERE k.tenant_id=%s::uuid AND k.agent_id=%s::uuid AND k.active=TRUE AND t.status='active'
      AND k.provenance->'lesson'->>'operation'=%s AND k.provenance->'lesson'->>'language'=%s
      AND k.provenance->'lesson'->>'ui_version'=%s AND k.provenance->>'review_status'='approved'
      AND k.provenance->>'approved_by' IS NOT NULL ORDER BY k.updated_at DESC,k.id LIMIT 1""",(tenant,agent,OPERATION,language,ui_version))
    row=cur.fetchone()
    if not row:return None
    lesson=validate(json.loads(row['content']))
    if lesson['review_status']!='approved' or lesson['language']!=language or lesson['verification']['ui_version']!=ui_version: return None
    return {'source_id':str(row['id']),'lesson':lesson}


def internal_attachment(lesson,*,tenant_id,expected_tenant_id,source_id,dialog_state,group_revision,live_group_revision):
    """Server-only proposed call_expert attachment. Not added to current expert result parser."""
    if identifier(tenant_id)!=identifier(expected_tenant_id): raise TenantLinearError('lesson_tenant_mismatch',403)
    if dialog_state not in {'ai','active','manual'}: raise TenantLinearError('lesson_parent_inactive',409)
    if group_revision is not None or live_group_revision is not None:
        if type(group_revision) is not int or type(live_group_revision) is not int or group_revision!=live_group_revision: raise TenantLinearError('lesson_revision_stale',409)
    value=validate(lesson)
    if value['review_status']!='approved': raise TenantLinearError('lesson_review_required',409)
    return {'type':'instruction_lesson','schema_version':1,'source_id':identifier(source_id),'lesson_id':value['id'],
            'operation':OPERATION,'language':value['language'],'verification':value['verification'],
            'delivery':'internal_only','customer_send_allowed':False,'changes_dialog_state':False}
