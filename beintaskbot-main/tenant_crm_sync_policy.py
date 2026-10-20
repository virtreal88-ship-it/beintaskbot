"""Owner-only, default-off policy for provider CRM synchronization."""
from __future__ import annotations
import uuid
from tenant_platform_core import _connect, _ensure_schema, _json

FIELDS={
    ('kommo','contact'):{'name','phone','email'},
    ('kommo','deal'):{'title','pipeline_id','stage_id','contact_id'},
    ('beinsystems','deal'):{'title','pipeline_id','stage_id','contact_id','description'},
}
DIRECTIONS={'off','to_external','from_external','bidirectional'}

class SyncPolicyError(ValueError): pass

def _owner(profile):
    if not profile or profile.get('role')!='owner' or profile.get('active',True) is not True:
        raise SyncPolicyError('Yalnız şirkət sahibi CRM sinxronizasiyasını dəyişə bilər.')
    return str(uuid.UUID(str(profile.get('tenant_id') or ''))),int(profile.get('telegram_id') or 0)

def _capable(provider,entity,direction):
    if direction=='off':return True
    if (provider,entity) not in FIELDS:return False
    if provider=='beinsystems' and direction in {'from_external','bidirectional'}:return False
    return True

def read_settings(profile):
    tenant,_=_owner(profile)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT entity_type,provider,mode,primary_provider,sync_direction,allowed_fields,
                enabled_since,updated_at FROM saas_crm_provider_settings
                WHERE tenant_id=%s::uuid AND entity_type IN ('contact','deal') ORDER BY entity_type''',(tenant,))
            rows=cur.fetchall()
            cur.execute('''SELECT provider,entity_type,state,count(*)::int AS count
                FROM saas_crm_sync_outbox WHERE tenant_id=%s::uuid GROUP BY provider,entity_type,state''',(tenant,))
            statuses=cur.fetchall()
            cur.execute('''SELECT provider,entity_type,count(*)::int AS count FROM saas_crm_sync_conflicts
                WHERE tenant_id=%s::uuid AND resolved_at IS NULL GROUP BY provider,entity_type''',(tenant,))
            conflicts=cur.fetchall()
            cur.execute("SELECT provider,status FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider=ANY(%s)",(tenant,['kommo','beinsystems']))
            integrations={r['provider']:r['status'] for r in cur.fetchall()}
    return {'settings':[dict(r) for r in rows], 'outbox':statuses,'conflicts':conflicts,
        'integrations':integrations,
        'capabilities':{'kommo':{'contact':['to_external','from_external','bidirectional'],
                                 'deal':['to_external','from_external','bidirectional']},
                        'beinsystems':{'contact':[], 'deal':['to_external']}}}

def save_settings(profile,items):
    tenant,actor=_owner(profile)
    if not isinstance(items,list) or not 1<=len(items)<=2:raise SyncPolicyError('Sinxronizasiya ayarları düzgün deyil.')
    prepared=[];seen=set()
    for item in items:
        if not isinstance(item,dict) or set(item)-{'entity_type','provider','mode','primary_provider','sync_direction','allowed_fields'}:
            raise SyncPolicyError('Sinxronizasiya ayarları düzgün deyil.')
        entity=str(item.get('entity_type') or ''); provider=str(item.get('provider') or '')
        mode=str(item.get('mode') or ''); direction=str(item.get('sync_direction') or 'off')
        fields=item.get('allowed_fields') or []
        if entity not in {'contact','deal'} or entity in seen or provider not in {'kommo','beinsystems'}:
            raise SyncPolicyError('CRM mənbəyi düzgün deyil.')
        seen.add(entity)
        if mode not in {'beintaskbot','external','selective'} or direction not in DIRECTIONS:
            raise SyncPolicyError('Sinxronizasiya istiqaməti düzgün deyil.')
        if (mode=='beintaskbot' and direction not in {'off','to_external'}) or (mode=='external' and direction not in {'off','from_external'}):
            raise SyncPolicyError('Seçilmiş əsas CRM ilə istiqamət uyğun gəlmir.')
        if mode=='beintaskbot':primary='beintaskbot'
        elif mode=='external':primary=provider
        else:primary=str(item.get('primary_provider') or provider)
        if primary not in {'beintaskbot','kommo','beinsystems'}:raise SyncPolicyError('Əsas CRM düzgün deyil.')
        if direction!='off' and not _capable(provider,entity,direction):
            raise SyncPolicyError('Bu provider üçün tələb olunan istiqamət sənədləşdirilməyib.')
        if not isinstance(fields,list) or len(fields)>20 or not set(fields)<=FIELDS.get((provider,entity),set()):
            raise SyncPolicyError('Sinxronizasiya sahələri düzgün deyil.')
        if direction!='off' and not fields:raise SyncPolicyError('Ən azı bir sahəni seçin.')
        if direction!='off' and entity=='contact' and 'name' not in fields:
            raise SyncPolicyError('Əlaqə sinxronizasiyası üçün ad sahəsi tələb olunur.')
        if direction!='off' and entity=='deal' and not {'title','pipeline_id','stage_id'}<=set(fields):
            raise SyncPolicyError('Sövdələşmə üçün ad, satış xətti və mərhələ sahələrini seçin.')
        prepared.append((entity,provider,mode,primary,direction,sorted(set(fields))))
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            for entity,provider,mode,primary,direction,fields in prepared:
                cur.execute('''INSERT INTO saas_crm_provider_settings
                    (tenant_id,entity_type,provider,mode,primary_provider,sync_direction,allowed_fields,enabled_since,updated_by)
                    VALUES(%s::uuid,%s,%s,%s,%s,%s,%s::jsonb,CASE WHEN %s='off' THEN NULL ELSE now() END,%s)
                    ON CONFLICT(tenant_id,entity_type) DO UPDATE SET provider=EXCLUDED.provider,mode=EXCLUDED.mode,
                    primary_provider=EXCLUDED.primary_provider,sync_direction=EXCLUDED.sync_direction,
                    allowed_fields=EXCLUDED.allowed_fields,enabled_since=CASE
                      WHEN EXCLUDED.sync_direction='off' THEN NULL
                      WHEN saas_crm_provider_settings.sync_direction='off' THEN now()
                      ELSE saas_crm_provider_settings.enabled_since END,
                    updated_by=EXCLUDED.updated_by,updated_at=now()''',
                    (tenant,entity,provider,mode,primary,direction,_json(fields),direction,actor))
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,action,entity_type,entity_id,payload)
                VALUES(%s,%s::uuid,'crm_sync_policy_updated','crm_sync',%s,%s::jsonb)''',
                (uuid.uuid4(),tenant,'provider_settings',_json([{'entity_type':p[0],'provider':p[1],
                    'mode':p[2],'direction':p[4],'fields':p[5]} for p in prepared])))
        conn.commit()
    return read_settings(profile)
