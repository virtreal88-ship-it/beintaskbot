"""Tenant-scoped native deal CRUD with atomic scoped scalar sync receipts."""
from __future__ import annotations
import uuid

from tenant_platform_core import _connect, _ensure_schema
from tenant_policy import TenantPolicy
from tenant_crm_scalar_store import lock_admission, queue_edit, managed
from tenant_crm_native_access import current_access, scoped_sql, authorize_create, locked_resource, action_columns, public_row, CRMAccessError, require_defined_relation, related_scope_sql, require_local_relation


def _uuid(value, label):
    try:
        return str(uuid.UUID(str(value))) if value else None
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f'invalid {label}') from None


def _fields(title, pipeline_id, stage_id, *, contact_id=None, responsible_id=None,
            channel_id=None, conversation_id='', description=''):
    title = str(title or '').strip()
    pipeline_id, stage_id = str(pipeline_id or '').strip(), str(stage_id or '').strip()
    description = str(description or '').strip()
    conversation_id = str(conversation_id or '').strip()
    if not title or len(title) > 240 or not pipeline_id or len(pipeline_id) > 120 or not stage_id or len(stage_id) > 120 or len(description) > 12000:
        raise ValueError('invalid deal fields')
    if channel_id and not conversation_id or conversation_id and not channel_id:
        raise ValueError('channel and conversation must be provided together')
    if len(conversation_id) > 240:
        raise ValueError('invalid conversation id')
    responsible = int(responsible_id) if responsible_id not in (None, '') else None
    if responsible is not None and responsible <= 0:
        raise ValueError('invalid responsible')
    return (title, pipeline_id, stage_id, _uuid(contact_id, 'contact id'), responsible,
            _uuid(channel_id, 'channel id'), conversation_id, description)


def _related(cur, tenant_id, values, *, profile=None, previous=None):
    _, pipeline, stage, contact, responsible, channel, conversation, _ = values
    editing_existing = previous is not None
    previous = previous or {}
    if (editing_existing and profile is not None and TenantPolicy(profile).has_scoped_access('deals')
            and str(contact or '') != str(previous.get('contact_id') or '')):
        # The API's legacy outbox exports whole records. A relationship edit
        # must never fall back to that path while the narrow adapter is dormant.
        require_local_relation(cur, tenant_id, 'deal')
    scoped_initial_contact = (not editing_existing and contact and profile is not None
        and any(TenantPolicy(profile).has_scoped_access(module) for module in ('deals', 'customers')))
    if scoped_initial_contact:
        # Initial selection is authorized by deal C plus current contact R,
        # never contact U. Keep the target/department grant locked through insert.
        if not locked_resource(cur, profile, 'customers', 'view',
                'saas_crm_native_contacts', tenant_id, contact):
            raise CRMAccessError('contact_unavailable', 404)
        require_local_relation(cur, tenant_id, 'deal')
    if (str(channel or ''), conversation) != (str(previous.get('channel_id') or ''), previous.get('conversation_id') or ''):
        require_defined_relation(profile, 'deals', 'dialogs')
    cur.execute("""SELECT 1 FROM saas_tenant_pipeline_stages s JOIN saas_tenant_pipelines p
        USING (tenant_id,pipeline_id) WHERE s.tenant_id=%s::uuid AND s.pipeline_id=%s
        AND s.stage_id=%s AND p.active=TRUE""", (tenant_id, pipeline, stage))
    if not cur.fetchone():
        raise ValueError('pipeline or stage unavailable')
    if contact:
        cur.execute("SELECT 1 FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL", (tenant_id, contact))
        if not cur.fetchone(): raise ValueError('contact unavailable')
    if responsible:
        cur.execute("SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE", (tenant_id, responsible))
        if not cur.fetchone(): raise ValueError('responsible unavailable')
    if channel:
        cur.execute("SELECT 1 FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s", (tenant_id, channel, conversation))
        if not cur.fetchone(): raise ValueError('conversation unavailable')


def create_deal(tenant_id, title, pipeline_id, stage_id, *, contact_id=None, responsible_id=None,
                channel_id=None, conversation_id='', description='', deal_id=None, actor_id=None):
    fields = _fields(title, pipeline_id, stage_id, contact_id=contact_id, responsible_id=responsible_id,
                     channel_id=channel_id, conversation_id=conversation_id, description=description)
    ident = _uuid(deal_id, 'deal id') if deal_id else str(uuid.uuid4())
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            lock_admission(cur, tenant_id, actor_id)
            profile = current_access(cur, tenant_id, actor_id, 'deals', 'create')
            authorize_create(cur, profile, 'deals', tenant_id, actor_id)
            _related(cur, tenant_id, fields, profile=profile)
            if fields[5]:
                cur.execute("""SELECT id,title,pipeline_id,stage_id,contact_id,responsible_id,channel_id,conversation_id,description,created_at,updated_at
                    FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
                    AND conversation_id=%s AND archived_at IS NULL FOR UPDATE""", (tenant_id,fields[5],fields[6]))
                existing=cur.fetchone()
                if existing:
                    if profile is not None:
                        authorized = locked_resource(cur, profile, 'deals', 'create', 'saas_crm_native_deals', tenant_id, existing['id'])
                        if not authorized or (TenantPolicy(profile).has_scoped_access('deals')
                                and str(authorized.get('access_responsible_id')) != str(actor_id)):
                            raise ValueError('deal id conflict')
                    if str(existing['id']) != ident or (existing['title'],existing['pipeline_id'],existing['stage_id'],str(existing['contact_id'] or ''),existing['responsible_id'],str(existing['channel_id']),existing['conversation_id'],existing['description']) != (fields[0],fields[1],fields[2],fields[3] or '',fields[4],fields[5],fields[6],fields[7]):
                        raise ValueError('conversation already linked to another deal')
                    sync = queue_edit(cur, tenant_id, actor_id, profile, 'deal', existing, None, action='create')
                    if sync is not None: existing = {**existing, 'sync': sync}
                    conn.commit()
                    return existing
            cur.execute("""INSERT INTO saas_crm_native_deals
                (id,tenant_id,title,pipeline_id,stage_id,contact_id,responsible_id,channel_id,conversation_id,description,access_responsible_id)
                VALUES (%s::uuid,%s::uuid,%s,%s,%s,%s::uuid,%s,%s::uuid,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
                RETURNING id,title,pipeline_id,stage_id,contact_id,responsible_id,channel_id,conversation_id,description,created_at,updated_at""",
                (ident,tenant_id,*fields,actor_id))
            row = cur.fetchone()
            if row is None:
                cur.execute("SELECT id,title,pipeline_id,stage_id,contact_id,responsible_id,channel_id,conversation_id,description,created_at,updated_at FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL", (tenant_id, ident))
                row = cur.fetchone()
                if profile is not None:
                    row = locked_resource(cur, profile, 'deals', 'create', 'saas_crm_native_deals', tenant_id, ident)
                    if row and TenantPolicy(profile).has_scoped_access('deals') and str(row.get('access_responsible_id')) != str(actor_id):
                        row = None
                if not row or (row['title'],row['pipeline_id'],row['stage_id'],str(row['contact_id'] or ''),row['responsible_id'],str(row['channel_id'] or ''),row['conversation_id'],row['description']) != (fields[0],fields[1],fields[2],fields[3] or '',fields[4],fields[5] or '',fields[6],fields[7]): raise ValueError('deal id conflict')
            from tenant_crm_native_tasks import maybe_create_stage_task
            maybe_create_stage_task(cur,tenant_id,row,event_key='deal-created:'+str(row['id']))
            sync = queue_edit(cur, tenant_id, actor_id, profile, 'deal', row, None, action='create')
            if sync is not None: row = {**row, 'sync': sync}
        conn.commit()
    return public_row(row)


def public_deal(row, profile):
    from tenant_scoped_access import scoped_access_enabled
    result = public_row(row)
    if result is not None:
        result['scoped_relations'] = scoped_access_enabled()
        update = bool((result.get('actions') or {}).get('update'))
        result['relation_actions'] = {'link_company': update, 'unlink_company': update}
    return result


def get_deal(tenant_id, deal_id, *, actor_id=None):
    ident = _uuid(deal_id, 'deal id')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'deals', 'view')
            where, params = scoped_sql(profile, 'deals', 'view', 'd')
            columns, action_params = action_columns(profile, 'deals', 'd')
            # The allowed deal contains its related client information, even
            # when the separate contact/company directory is closed.
            cur.execute(f"""SELECT d.id,d.title,d.pipeline_id,d.stage_id,d.contact_id,c.display_name AS contact_name,
                c.phone AS contact_phone,c.email AS contact_email,
                d.company_id,co.name AS company_name,co.phone AS company_phone,co.email AS company_email,
                d.responsible_id,m.display_name AS responsible_name,
                d.channel_id,d.conversation_id,d.description,d.created_at,d.updated_at {columns}
                FROM saas_crm_native_deals d LEFT JOIN saas_crm_native_contacts c
                  ON c.tenant_id=d.tenant_id AND c.id=d.contact_id
                LEFT JOIN saas_crm_native_companies co ON co.tenant_id=d.tenant_id AND co.id=d.company_id
                LEFT JOIN saas_tenant_members m ON m.tenant_id=d.tenant_id AND m.telegram_id=d.responsible_id
                WHERE d.tenant_id=%s::uuid AND d.id=%s::uuid AND d.archived_at IS NULL AND ({where})""",
                [*action_params, tenant_id, ident, *params])
            return public_deal(cur.fetchone(), profile)


def list_deals(tenant_id, search='', limit=50, *, actor_id=None):
    needle = '%' + str(search or '').strip()[:120].replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'deals', 'view')
            where, params = scoped_sql(profile, 'deals', 'view', 'd')
            columns, action_params = action_columns(profile, 'deals', 'd')
            cur.execute(f"""SELECT d.id,d.title,d.pipeline_id,d.stage_id,d.contact_id,c.display_name AS contact_name,
                d.company_id,co.name AS company_name,d.responsible_id,d.channel_id,d.conversation_id,d.updated_at {columns}
                FROM saas_crm_native_deals d
                LEFT JOIN saas_crm_native_contacts c ON c.tenant_id=d.tenant_id AND c.id=d.contact_id
                LEFT JOIN saas_crm_native_companies co ON co.tenant_id=d.tenant_id AND co.id=d.company_id
                WHERE d.tenant_id=%s::uuid AND d.archived_at IS NULL AND ({where})
                AND (d.title ILIKE %s ESCAPE '\\' OR c.display_name ILIKE %s ESCAPE '\\')
                ORDER BY d.updated_at DESC,d.id LIMIT %s""",
                [*action_params, tenant_id, *params, needle, needle, max(1,min(int(limit),100))])
            return [public_deal(row, profile) for row in cur.fetchall()]


def deal_options(tenant_id, *, actor_id=None, action='view'):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'deals', action)
            cur.execute("""SELECT p.pipeline_id,p.name AS pipeline_name,s.stage_id,s.name AS stage_name,s.sort_order
                FROM saas_tenant_pipelines p JOIN saas_tenant_pipeline_stages s
                USING (tenant_id,pipeline_id) WHERE p.tenant_id=%s::uuid AND p.active=TRUE
                ORDER BY p.name,s.sort_order,s.name""",(tenant_id,))
            workflows=cur.fetchall()
            cur.execute("SELECT telegram_id,display_name FROM saas_tenant_members WHERE tenant_id=%s::uuid AND active=TRUE ORDER BY display_name",(tenant_id,))
            members=cur.fetchall()
            scoped_contact_selection = profile is not None and any(
                TenantPolicy(profile).has_scoped_access(module) for module in ('deals','customers'))
            where, params = (scoped_sql(profile, 'customers', 'view', 'c') if scoped_contact_selection
                             else related_scope_sql(profile, 'customers', 'c'))
            cur.execute(f"""SELECT c.id,c.display_name FROM saas_crm_native_contacts c
              WHERE c.tenant_id=%s::uuid AND c.archived_at IS NULL AND ({where})
              ORDER BY c.display_name LIMIT 250""", [tenant_id, *params])
            contacts=cur.fetchall()
    from tenant_scoped_access import scoped_access_enabled
    update = profile is not None and TenantPolicy(profile).allows_action('deals','update')
    return {'workflows':workflows,'members':members,'contacts':contacts,
            'scoped_relations':scoped_access_enabled(),
            'relation_actions':{'link_company':update,'unlink_company':update}}


def update_deal(tenant_id, deal_id, title, pipeline_id, stage_id, *, contact_id=None, responsible_id=None,
                channel_id=None, conversation_id='', description='', actor_id=None):
    ident = _uuid(deal_id, 'deal id')
    fields = _fields(title,pipeline_id,stage_id,contact_id=contact_id,responsible_id=responsible_id,
                     channel_id=channel_id,conversation_id=conversation_id,description=description)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            from tenant_crm_writer_fence import lock_card
            from tenant_scoped_access import scoped_access_enabled
            if actor_id is not None and scoped_access_enabled():
                lock_card(cur, tenant_id, 'deal', ident)
            lock_admission(cur, tenant_id, actor_id)
            profile = current_access(cur, tenant_id, actor_id, 'deals', 'update')
            if actor_id is not None:
                previous = locked_resource(cur, profile, 'deals', 'update', 'saas_crm_native_deals', tenant_id, ident)
                if not previous:
                    return None
                _related(cur, tenant_id, fields, profile=profile, previous=previous)
            else:
                _related(cur, tenant_id, fields)
                cur.execute("SELECT stage_id FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR UPDATE",(tenant_id,ident))
                previous=cur.fetchone()
            if (previous and managed(profile, 'deal')
                    and (previous['title'], previous['pipeline_id'], previous['stage_id'],
                         str(previous.get('contact_id') or '') or None, previous.get('responsible_id'),
                         str(previous.get('channel_id') or '') or None,
                         previous.get('conversation_id') or '', previous.get('description') or '') == fields):
                row = {key:previous[key] for key in ('id','title','pipeline_id','stage_id','contact_id',
                    'responsible_id','channel_id','conversation_id','description','created_at','updated_at')}
                row['sync'] = queue_edit(cur, tenant_id, actor_id, profile, 'deal', row, previous)
                conn.commit()
                return row
            stamp_sql = 'clock_timestamp()' if managed(profile, 'deal') else 'now()'
            cur.execute(f"""UPDATE saas_crm_native_deals SET title=%s,pipeline_id=%s,stage_id=%s,contact_id=%s::uuid,
                responsible_id=%s,channel_id=%s::uuid,conversation_id=%s,description=%s,updated_at={stamp_sql}
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL
                RETURNING id,title,pipeline_id,stage_id,contact_id,responsible_id,channel_id,conversation_id,description,created_at,updated_at""",
                (*fields[:3], fields[3],fields[4],fields[5],fields[6],fields[7],tenant_id,ident))
            row=cur.fetchone()
            if row and previous and str(previous['stage_id']) != str(row['stage_id']):
                from tenant_crm_native_tasks import maybe_create_stage_task
                stamp=row['updated_at'].isoformat() if row.get('updated_at') else str(uuid.uuid4())
                maybe_create_stage_task(cur,tenant_id,row,event_key=f"stage-entered:{row['id']}:{previous['stage_id']}:{row['stage_id']}:{stamp}")
            if row:
                sync = queue_edit(cur, tenant_id, actor_id, profile, 'deal', row, previous)
                if sync is not None: row = {**row, 'sync': sync}
        conn.commit()
    return row


def archive_deal(tenant_id, deal_id, *, actor_id=None):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'deals', 'delete')
            if actor_id is not None and not locked_resource(cur, profile, 'deals', 'delete',
                    'saas_crm_native_deals', tenant_id, _uuid(deal_id, 'deal id')):
                return False
            cur.execute("UPDATE saas_crm_native_deals SET archived_at=now(),updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id",(tenant_id,_uuid(deal_id,'deal id')))
            row=cur.fetchone()
        conn.commit()
    return row is not None


def find_chat_deal(tenant_id,channel_id,conversation_id):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id,title,pipeline_id,stage_id,contact_id,responsible_id,company_id,channel_id,conversation_id,description FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s AND archived_at IS NULL",(tenant_id,_uuid(channel_id,'channel id'),str(conversation_id)))
            return cur.fetchone()


def set_deal_stage(tenant_id,deal_id,stage_id):
    ident=_uuid(deal_id,'deal id'); stage=str(stage_id or '').strip()
    if not stage:raise ValueError('invalid stage')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT pipeline_id FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR UPDATE",(tenant_id,ident)); row=cur.fetchone()
            if not row:return None
            cur.execute("SELECT 1 FROM saas_tenant_pipeline_stages s JOIN saas_tenant_pipelines p USING(tenant_id,pipeline_id) WHERE s.tenant_id=%s::uuid AND s.pipeline_id=%s AND s.stage_id=%s AND p.active=TRUE",(tenant_id,row['pipeline_id'],stage))
            if not cur.fetchone():raise ValueError('stage outside deal pipeline')
            cur.execute("UPDATE saas_crm_native_deals SET stage_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING id,title,pipeline_id,stage_id,contact_id,responsible_id,company_id,channel_id,conversation_id",(stage,tenant_id,ident)); result=cur.fetchone()
        conn.commit()
    return result


def assign_deal_responsible(tenant_id,deal_id,responsible_id):
    ident=_uuid(deal_id,'deal id')
    try:assignee=int(responsible_id)
    except (ValueError,TypeError):raise ValueError('invalid responsible') from None
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE",(tenant_id,assignee))
            if not cur.fetchone():raise ValueError('responsible unavailable')
            cur.execute("UPDATE saas_crm_native_deals SET responsible_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id,title,pipeline_id,stage_id,contact_id,responsible_id,company_id,channel_id,conversation_id",(assignee,tenant_id,ident)); row=cur.fetchone()
        conn.commit()
    return row


def set_deal_company_link(tenant_id, deal_id, company_id, *, unlink=False, actor_id):
    """Source-specific deal U authorizes only its company FK change."""
    from tenant_scoped_access import scoped_access_enabled
    if not scoped_access_enabled():
        raise CRMAccessError('scoped_relation_unavailable', 403)
    deal, company = _uuid(deal_id, 'deal id'), _uuid(company_id, 'company id')
    if not deal or not company:
        raise ValueError('deal and company required')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'deals', 'update')
            row = locked_resource(cur, profile, 'deals', 'update',
                'saas_crm_native_deals', tenant_id, deal)
            if not row or (unlink and str(row.get('company_id') or '') != company):
                return None
            # Both card directions lock deal before company. Target data and
            # target update/delete authority never pass through this route.
            cur.execute("""SELECT id FROM saas_crm_native_companies
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR SHARE""",
                (tenant_id, company))
            if not cur.fetchone():
                return None
            require_local_relation(cur, tenant_id, 'deal')
            cur.execute("""UPDATE saas_crm_native_deals SET company_id=%s::uuid,updated_at=clock_timestamp()
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL""",
                (None if unlink else company, tenant_id, deal))
        conn.commit()
    return {'id': deal, 'company_id': company, 'linked': not unlink}
