"""Manual customer-memory facts for explicitly confirmed direct routes.

No AI calls, background processing or shared summaries. Every entry is scoped
by its exact source and checked again on read. The feature is disabled unless
both the process and tenant switches are enabled.
"""
import hashlib
import json
import os
import re
import uuid

from tenant_platform import _connect, _ensure_schema
from tenant_linear_policy import TenantLinearError, identifier
from tenant_operator_access import access, assert_route
from tenant_connect_identity import conflicted
from tenant_ai_agents_store import _audit

_CATEGORIES = {'profile','feature','topic','problem','agreement'}
_SOURCE_KINDS = {'customer_statement','employee_observation'}
_STATUSES = {'confirmed','needs_review','outdated','refuted'}
_SECRET = re.compile(r'(?i)(?:\b(?:password|passwd|passcode|token|secret|api[ _-]?key|bearer|authorization|парол\w*|токен\w*|şifrə|sifre|kart\s+nömrəsi|cvv|cvc)\b|\bsk-[a-z0-9_-]{12,}|(?:\d[ -]?){13,19})')


def _guard(cur, profile, action):
    tenant, actor, policy = access(cur, profile, 'dialogs')
    if os.environ.get('CUSTOMER_MEMORY_ENABLED') != '1':
        raise TenantLinearError('Customer memory unavailable.', 404)
    cur.execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='customer_memory'",(tenant,))
    row=cur.fetchone()
    if not row or not isinstance(row['value'],dict) or set(row['value']) != {'enabled'} or row['value']['enabled'] is not True:
        raise TenantLinearError('Customer memory unavailable.', 404)
    if policy.profile.get('role') != 'owner' and not isinstance(policy.profile.get('permissions'),list):
        raise TenantLinearError('Customer memory unavailable.',404)
    if not policy.allows_action('customers',action):
        raise TenantLinearError('Customer memory unavailable.', 404)
    return tenant,actor,policy


def _text(value):
    if not isinstance(value,str) or not (1 <= len(value.strip()) <= 1200):
        raise TenantLinearError('Invalid fact.',400)
    value=value.strip()
    if _SECRET.search(value) or any(ord(ch)<32 and ch not in '\n\t' for ch in value):
        raise TenantLinearError('Sensitive or invalid fact.',400)
    return value


def _route(cur, tenant, policy, channel, conversation):
    channel=identifier(channel)
    if not isinstance(conversation,str) or not re.fullmatch(r'[1-9][0-9]{5,22}@(s\.whatsapp\.net|c\.us|lid)',conversation):
        raise TenantLinearError('Ambiguous identity.',409)
    route=assert_route(cur,tenant,policy,'connect',conversation,channel)
    # The operator history projection requires an existing channel and hides
    # merged source routes; a tenant-only route lookup is not sufficient.
    cur.execute('''SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid
      AND connect_channel_id=%s::uuid''',(tenant,channel))
    if not cur.fetchone(): raise TenantLinearError('Source unavailable.',404)
    if route.get('conversation_kind') == 'group' or conflicted(cur,tenant,channel,conversation):
        raise TenantLinearError('Ambiguous identity.',409)
    return channel, route


def _subject(cur,tenant,contact,*,allow_archived=False):
    contact=identifier(contact)
    cur.execute('''SELECT id FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid
      AND id=%s::uuid AND (archived_at IS NULL OR %s) FOR SHARE''',(tenant,contact,allow_archived))
    if not cur.fetchone(): raise TenantLinearError('Customer unavailable.',404)
    return contact


def _source(cur,tenant,link,source_id):
    source_id=identifier(source_id)
    cur.execute('''SELECT id,body,message_type,direction,happened_at,raw,xmin::text AS row_revision FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND kommo_lead_id=%s AND channel=%s FOR SHARE''',
      (tenant,source_id,link['lead_id'],'connect:'+str(link['channel_id'])))
    row=cur.fetchone()
    if not row or row['message_type'] != 'text' or not row['body']:
        raise TenantLinearError('Source unavailable.',404)
    payload={k:row.get(k) for k in ('body','message_type','direction','happened_at','raw','row_revision')}
    version=hashlib.sha256(json.dumps(payload,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()
    return row,version


def _link(cur,tenant,policy,contact,link_id,*,lock='SHARE'):
    cur.execute('''SELECT * FROM saas_customer_memory_links WHERE tenant_id=%s::uuid AND contact_id=%s::uuid
      AND id=%s::uuid FOR ''' + ('UPDATE' if lock=='UPDATE' else 'SHARE'),(tenant,contact,identifier(link_id)))
    link=cur.fetchone()
    if not link or link['state']!='confirmed': raise TenantLinearError('Source unavailable.',404)
    _,route=_route(cur,tenant,policy,str(link['channel_id']),link['conversation_id'])
    if int(route['lead_id']) != int(link['lead_id']): raise TenantLinearError('Ambiguous identity.',409)
    return link


def confirm_link(profile, contact, channel, conversation, confirm):
    if confirm is not True: raise TenantLinearError('Confirmation required.',400)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=_guard(cur,profile,'update')
            if policy.profile.get('role')!='owner': raise TenantLinearError('Owner required.',403)
            contact=_subject(cur,tenant,contact)
            channel,route=_route(cur,tenant,policy,channel,conversation)
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('customer-memory-link:'+tenant+':'+channel+':'+conversation,))
            cur.execute('''SELECT id,contact_id FROM saas_customer_memory_links WHERE tenant_id=%s::uuid
              AND channel_id=%s::uuid AND conversation_id=%s AND state='confirmed' FOR UPDATE''',(tenant,channel,conversation))
            old=cur.fetchone()
            if old:
                if str(old['contact_id']) != contact: raise TenantLinearError('Ambiguous identity.',409)
                return {'id':str(old['id'])}
            link=str(uuid.uuid4())
            cur.execute('''INSERT INTO saas_customer_memory_links
              (id,tenant_id,contact_id,channel_id,conversation_id,lead_id,state,confirmed_by)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,'confirmed',%s)''',
              (link,tenant,contact,channel,conversation,route['lead_id'],actor))
            _audit(cur,tenant,actor,'customer_memory_link_confirmed','customer_memory_link',link)
        conn.commit()
    return {'id':link}


def revoke_link(profile,contact,link_id,expected_version):
    if type(expected_version) is not int: raise TenantLinearError('Invalid version.',400)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=_guard(cur,profile,'update')
            if policy.profile.get('role')!='owner': raise TenantLinearError('Owner required.',403)
            contact=_subject(cur,tenant,contact)
            # Revocation remains possible even if the route becomes inaccessible or conflicted.
            cur.execute('''UPDATE saas_customer_memory_links SET state='revoked',version=version+1,
              revoked_by=%s,revoked_at=now() WHERE tenant_id=%s::uuid AND contact_id=%s::uuid
              AND id=%s::uuid AND state='confirmed' AND version=%s RETURNING id''',
              (actor,tenant,contact,identifier(link_id),expected_version))
            if not cur.fetchone():raise TenantLinearError('Link changed or unavailable.',409)
            _audit(cur,tenant,actor,'customer_memory_link_revoked','customer_memory_link',link_id)
        conn.commit()
    return {'revoked':True}


def add_fact(profile,contact,data):
    if not isinstance(data,dict) or set(data)!={'request_id','link_id','source_message_id','category','value','source_kind','reliability'}:
        raise TenantLinearError('Invalid fact.',400)
    if data['category'] not in _CATEGORIES or data['source_kind'] not in _SOURCE_KINDS or data['reliability'] not in _STATUSES:
        raise TenantLinearError('Invalid fact.',400)
    value=_text(data['value']); request_id=identifier(data['request_id'])
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=_guard(cur,profile,'update'); contact=_subject(cur,tenant,contact)
            # Client request identifiers never address another tenant's primary key.
            fact=str(uuid.uuid5(uuid.UUID(tenant),request_id))
            link=_link(cur,tenant,policy,contact,data['link_id'],lock='UPDATE')
            source,source_version=_source(cur,tenant,link,data['source_message_id'])
            raw=source.get('raw') if isinstance(source.get('raw'),dict) else {}
            if data['source_kind']=='customer_statement' and (source['direction']!='incoming'
                    or raw.get('verified_employee_id') is not None or raw.get('fromMe') is True
                    or raw.get('ai_authored') is True or raw.get('run_id')):
                raise TenantLinearError('Source direction mismatch.',400)
            cur.execute('''SELECT 1 FROM saas_customer_memory_suppressions WHERE tenant_id=%s::uuid
              AND contact_id=%s::uuid AND source_message_id=%s::uuid''',(tenant,contact,str(source['id'])))
            if cur.fetchone(): raise TenantLinearError('Source suppressed.',409)
            cur.execute('''SELECT id,version,category,value,source_kind,reliability,link_id,source_message_id FROM saas_customer_memory_facts
              WHERE tenant_id=%s::uuid AND id=%s::uuid''',(tenant,fact))
            existing=cur.fetchone()
            if existing:
                if (str(existing['link_id']) != str(link['id']) or str(existing['source_message_id']) != str(source['id'])
                        or existing['value'] != value
                        or any(existing[k] != data[k] for k in ('category','source_kind','reliability'))):
                    raise TenantLinearError('Request conflict.',409)
                return {'id':fact,'version':existing['version']}
            cur.execute('''SELECT 1 FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
              AND contact_id=%s::uuid AND source_message_id=%s::uuid''',(tenant,contact,str(source['id'])))
            if cur.fetchone(): raise TenantLinearError('Source already used.',409)
            cur.execute('''SELECT count(*) AS n FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
              AND link_id=%s::uuid''',(tenant,str(link['id'])))
            if cur.fetchone()['n']>=200: raise TenantLinearError('Memory limit reached.',409)
            cur.execute('''INSERT INTO saas_customer_memory_facts(id,tenant_id,contact_id,link_id,link_version,
              source_message_id,source_version,category,value,source_kind,reliability,event_at,last_confirmed_at)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s,%s,
                CASE WHEN %s='confirmed' THEN now() END)''',
              (fact,tenant,contact,str(link['id']),link['version'],str(source['id']),source_version,
               data['category'],value,data['source_kind'],data['reliability'],source['happened_at'],data['reliability']))
            cur.execute('''INSERT INTO saas_customer_memory_fact_versions(fact_id,version,value,reliability,actor)
              VALUES(%s::uuid,1,%s,%s,%s)''',(fact,value,data['reliability'],actor))
            _audit(cur,tenant,actor,'customer_memory_fact_added','customer_memory_fact',fact)
        conn.commit()
    return {'id':fact,'version':1}


def read(profile,contact):
    """Read only facts whose source, route, identity and current rights pass."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_,policy=_guard(cur,profile,'view');contact=_subject(cur,tenant,contact)
            cur.execute('''SELECT id FROM saas_customer_memory_links WHERE tenant_id=%s::uuid
              AND contact_id=%s::uuid AND state='confirmed' ORDER BY id''',(tenant,contact))
            candidates=cur.fetchall(); links=[]; facts=[]; scanned=0
            for candidate in candidates:
                try:
                    link=_link(cur,tenant,policy,contact,str(candidate['id']))
                except TenantLinearError:
                    continue
                if len(links) >= 50: break
                links.append({'id':str(link['id']),'version':link['version'],
                              'channel_id':str(link['channel_id']),'conversation_id':link['conversation_id']})
                if scanned >= 100: continue
                cur.execute('''SELECT * FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
                  AND contact_id=%s::uuid AND link_id=%s::uuid ORDER BY updated_at DESC,id LIMIT 50''',
                  (tenant,contact,str(link['id'])))
                for fact in cur.fetchall():
                    if len(facts) >= 50 or scanned >= 100: break
                    scanned+=1
                    if fact['link_version']!=link['version']: continue
                    try: _,version=_source(cur,tenant,link,str(fact['source_message_id']))
                    except TenantLinearError: continue
                    if version!=fact['source_version']: continue
                    facts.append({k:fact[k] for k in ('category','value','source_kind','reliability','event_at',
                      'observed_at','valid_from','valid_to','last_confirmed_at','version','updated_at')})
                    facts[-1].update(id=str(fact['id']),link_id=str(link['id']),
                                     source_message_id=str(fact['source_message_id']))
            facts.sort(key=lambda x:(x['updated_at'],x['id']),reverse=True)
            # Bound the output; never return counts or versions from hidden sources.
            return {'links':links[:50],'facts':facts[:50]}


def change_fact(profile,contact,fact_id,expected_version,action,*,value=None):
    if type(expected_version) is not int or action not in {'edit','confirm','outdated','refute','delete'}:
        raise TenantLinearError('Invalid fact change.',400)
    if action=='edit': value=_text(value)
    elif value is not None: raise TenantLinearError('Invalid fact change.',400)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            permission='delete' if action=='delete' else 'update'
            tenant,actor,policy=_guard(cur,profile,permission)
            owner_delete=action=='delete' and policy.profile.get('role')=='owner'
            contact=_subject(cur,tenant,contact,allow_archived=owner_delete)
            fact_id=identifier(fact_id)
            cur.execute('''SELECT link_id FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
              AND contact_id=%s::uuid AND id=%s::uuid''',(tenant,contact,fact_id))
            ref=cur.fetchone()
            if not ref:raise TenantLinearError('Fact unavailable.',404)
            if owner_delete:
                cur.execute('''SELECT * FROM saas_customer_memory_links WHERE tenant_id=%s::uuid
                  AND contact_id=%s::uuid AND id=%s::uuid FOR UPDATE''',(tenant,contact,str(ref['link_id'])))
                link=cur.fetchone()
                if not link: raise TenantLinearError('Fact unavailable.',404)
            else:
                link=_link(cur,tenant,policy,contact,str(ref['link_id']),lock='UPDATE')
            cur.execute('''SELECT * FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
              AND contact_id=%s::uuid AND id=%s::uuid FOR UPDATE''',(tenant,contact,fact_id))
            fact=cur.fetchone()
            if not fact or fact['version']!=expected_version or (not owner_delete and fact['link_version']!=link['version']):
                raise TenantLinearError('Fact changed.',409)
            if not owner_delete:
                _,source_version=_source(cur,tenant,link,str(fact['source_message_id']))
                if source_version!=fact['source_version']:raise TenantLinearError('Source changed.',409)
            if action=='delete':
                cur.execute('''INSERT INTO saas_customer_memory_suppressions(tenant_id,contact_id,source_message_id)
                  VALUES(%s::uuid,%s::uuid,%s::uuid) ON CONFLICT DO NOTHING''',
                  (tenant,contact,str(fact['source_message_id'])))
                cur.execute('DELETE FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,fact_id))
            else:
                new_value=value if action=='edit' else fact['value']
                status={'edit':'needs_review','confirm':'confirmed','outdated':'outdated','refute':'refuted'}[action]
                cur.execute('''UPDATE saas_customer_memory_facts SET value=%s,reliability=%s,version=version+1,
                  last_confirmed_at=CASE WHEN %s='confirmed' THEN now() ELSE last_confirmed_at END,
                  updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING version''',
                  (new_value,status,status,tenant,fact_id))
                version=cur.fetchone()['version']
                cur.execute('''INSERT INTO saas_customer_memory_fact_versions(fact_id,version,value,reliability,actor)
                  VALUES(%s::uuid,%s,%s,%s,%s)''',(fact_id,version,new_value,status,actor))
            _audit(cur,tenant,actor,'customer_memory_fact_'+action,'customer_memory_fact',fact_id)
        conn.commit()
    return {'deleted':True} if action=='delete' else {'id':fact_id,'version':version}
