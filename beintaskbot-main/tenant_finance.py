"""Append-only tenant ledger with idempotent manual commands; no real payouts."""
import hashlib
import json
import uuid
from tenant_platform import _connect
from tenant_finance_schema import ensure_finance_schema
from tenant_finance_policy import finance_policy,entry_id,minor_amount,money_text,TenantFinanceError
from tenant_policy import positive_id


def public_entry(row: dict) -> dict:
    result={key:value for key,value in row.items() if key not in {'fingerprint','request_id'}}
    for key in ('id','tenant_id','reverses_id','created_at','hot_order_id'):
        if result.get(key) is not None:
            result[key]=result[key].isoformat() if hasattr(result[key],'isoformat') else str(result[key])
    result['amount']=money_text(int(result.pop('amount_minor')))
    return result


def list_members(profile: dict, *, limit: int=100, offset: int=0) -> dict:
    policy=finance_policy(profile,write=True)
    with _connect() as conn:
        ensure_finance_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT telegram_id,display_name,active FROM saas_tenant_members
                WHERE tenant_id=%s::uuid ORDER BY display_name,telegram_id LIMIT %s OFFSET %s''',
                (str(policy.profile['tenant_id']),max(1,min(int(limit),100)),max(0,min(int(offset),100000))))
            rows=cur.fetchall()
    return {'members':rows}


def history(profile: dict, *, member_id: object=None, limit: int=50, offset: int=0) -> dict:
    policy=finance_policy(profile)
    member=positive_id(member_id if member_id is not None else profile['telegram_id'])
    if not member:
        raise TenantFinanceError('Əməkdaşı seçin.')
    if not policy.privileged and member!=positive_id(profile['telegram_id']):
        raise TenantFinanceError('Yalnız öz balansınızı görə bilərsiniz.',403)
    tenant=str(profile['tenant_id']);limit=max(1,min(int(limit),100));offset=max(0,min(int(offset),100000))
    with _connect() as conn:
        ensure_finance_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cur.execute('SELECT display_name,active FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s',(tenant,member))
            person=cur.fetchone()
            if not person:
                raise TenantFinanceError('Əməkdaş tapılmadı.',404)
            # One snapshot keeps balance, count and the page consistent while writes occur.
            cur.execute('''SELECT COALESCE(sum(amount_minor),0) AS balance,count(*) AS total
                FROM saas_finance_entries WHERE tenant_id=%s::uuid AND member_id=%s''',(tenant,member))
            summary=cur.fetchone()
            cur.execute('''SELECT e.*,m.display_name AS actor_name FROM saas_finance_entries e
                JOIN saas_tenant_members m ON m.tenant_id=e.tenant_id AND m.telegram_id=e.actor_id
                WHERE e.tenant_id=%s::uuid AND e.member_id=%s ORDER BY e.created_at DESC,e.id DESC LIMIT %s OFFSET %s''',
                (tenant,member,limit,offset))
            rows=cur.fetchall()
    return {'entries':[public_entry(row) for row in rows],'balance':money_text(int(summary['balance'])),
            'total':int(summary['total']),'currency':'AZN','member_id':member,'display_name':person['display_name'],
            'active':person['active'],'can_write':policy.privileged,'can_view_all':policy.privileged}


def record(profile: dict, data: dict) -> dict:
    policy=finance_policy(profile,write=True)
    action=data.get('action')
    if action not in {'credit','debit','reverse'}:
        raise TenantFinanceError('Əməliyyat növü düzgün deyil.')
    member=positive_id(data.get('member_id'));request=entry_id(data.get('request_id'))
    note=data.get('note','')
    if not member or not isinstance(note,str) or not note.strip() or len(note.strip())>2000:
        raise TenantFinanceError('Əməkdaş və əməliyyat səbəbi lazımdır.')
    source=entry_id(data.get('entry_id')) if action=='reverse' else None
    amount=None if source else minor_amount(data.get('amount'))*(1 if action=='credit' else -1)
    stamp=hashlib.sha256(json.dumps({'member':member,'action':action,'amount':amount,'source':source,'note':note.strip()},sort_keys=True).encode()).hexdigest()
    tenant,user=str(profile['tenant_id']),int(profile['telegram_id'])
    with _connect() as conn:
        ensure_finance_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('finance:'+tenant+':'+str(user)+':'+request,))
            cur.execute('SELECT * FROM saas_finance_entries WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid',(tenant,user,request))
            previous=cur.fetchone()
            if previous:
                if previous['fingerprint']!=stamp:
                    raise TenantFinanceError('Sorğu ID-si başqa əməliyyatda istifadə edilib.',409)
                return public_entry(previous)
            # Serializes all writers for this company's employee, not globally by Telegram ID.
            cur.execute('SELECT active FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s FOR UPDATE',(tenant,member))
            recipient=cur.fetchone()
            if not recipient or (recipient['active'] is not True and action!='reverse'):
                raise TenantFinanceError('Aktiv əməkdaş tapılmadı.',404)
            if source:
                cur.execute('SELECT * FROM saas_finance_entries WHERE tenant_id=%s::uuid AND member_id=%s AND id=%s::uuid',(tenant,member,source))
                original=cur.fetchone()
                if not original or original['kind']=='reverse':
                    raise TenantFinanceError('Əks əməliyyat üçün qeyd tapılmadı.',404)
                cur.execute('SELECT id FROM saas_finance_entries WHERE tenant_id=%s::uuid AND reverses_id=%s::uuid',(tenant,source))
                if cur.fetchone():
                    raise TenantFinanceError('Bu əməliyyat artıq geri alınıb.',409)
                amount=-int(original['amount_minor'])
            cur.execute('SELECT COALESCE(sum(amount_minor),0) AS balance FROM saas_finance_entries WHERE tenant_id=%s::uuid AND member_id=%s',(tenant,member))
            balance=int(cur.fetchone()['balance'])
            if balance+amount<0 and not (policy.policies.get('finance') or {}).get('allow_negative_balances',False):
                raise TenantFinanceError('Balans kifayət etmir. Mənfi balans şirkət qaydası ilə bağlıdır.',409)
            cur.execute('''INSERT INTO saas_finance_entries
                (tenant_id,id,member_id,actor_id,request_id,fingerprint,kind,amount_minor,note,reverses_id)
                VALUES (%s::uuid,%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s,%s::uuid) RETURNING *''',
                (tenant,str(uuid.uuid4()),member,user,request,stamp,action,amount,note.strip(),source))
            result=public_entry(cur.fetchone())
            cur.execute('''INSERT INTO saas_tenant_audit_events (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                VALUES (%s::uuid,%s::uuid,%s,%s,'finance_entry',%s,%s::jsonb)''',
                (str(uuid.uuid4()),tenant,user,'finance_'+action,result['id'],json.dumps({'member_id':member,'amount':result['amount']})))
        conn.commit()
    return result
