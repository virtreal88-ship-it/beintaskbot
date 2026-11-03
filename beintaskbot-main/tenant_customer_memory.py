"""Manual customer-memory facts for explicitly confirmed direct routes.

No AI calls, background processing or shared summaries. Every entry is scoped
by its exact source and checked again on read. The feature is disabled unless
both the process and tenant switches are enabled.
"""
import hashlib
import base64
import hmac
import json
import os
import re
import uuid
import unicodedata
import time
import psycopg

from tenant_platform import _connect, _ensure_schema
from tenant_linear_policy import TenantLinearError, identifier
from tenant_operator_access import access, assert_route
from tenant_connect_identity import conflicted
from tenant_ai_agents_store import _audit

_CATEGORIES = {'profile','feature','topic','problem','agreement'}
_SOURCE_KINDS = {'customer_statement','employee_observation'}
_STATUSES = {'confirmed','needs_review','outdated','refuted'}
_SECRET = re.compile(r'(?i)(?:\b(?:password|passwd|passcode|pin|recovery|seed|mnemonic|secret|token|api[ _-]?key|bearer|authorization|iban|парол\w*|токен\w*|şifrə|sifre|kart\s+nömrəsi|cvv|cvc)\b|\bsk-[a-z0-9_-]{12,}|\b[A-Z]{2}\d{2}[A-Z0-9 ]{11,30}\b|(?:\d[ -]?){13,19})')


def _guard(cur, profile, action, *, erasure_recovery=False):
    tenant, actor, policy = access(cur, profile, 'dialogs')
    cur.execute("SELECT current_setting('transaction_isolation') AS isolation")
    if cur.fetchone()['isolation']!='read committed':
        raise TenantLinearError('Memory isolation unavailable.',503)
    if not erasure_recovery:
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
    if erasure_recovery and (action!='delete' or policy.profile.get('role')!='owner'):
        raise TenantLinearError('Customer memory unavailable.', 404)
    return tenant,actor,policy


def _text(value):
    if not isinstance(value,str) or not (1 <= len(value.strip()) <= 1200):
        raise TenantLinearError('Invalid fact.',400)
    value=value.strip()
    normalized=unicodedata.normalize('NFKC',value)
    if (_SECRET.search(normalized) or re.search(r'(?i)(?:код\s+доступа|giriş\s+kodu|giris\s+kodu|пар[о0]л)',normalized)
            or any(unicodedata.category(ch) in {'Cf','Cc'} and ch not in '\n\t' for ch in value)):
        raise TenantLinearError('Sensitive or invalid fact.',400)
    return value


def _lock_fact_ids(cur,tenant,ids):
    # Every new writer locks the same tenant/fact IDs in sorted order. This
    # prevents a reused client request_id from restoring an erased fact ID.
    for fact_id in sorted(set(ids)):
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                    ('customer-memory-fact:'+tenant+':'+fact_id,))


def _erasure_request_id(tenant,contact,fact_id,version):
    return str(uuid.uuid5(uuid.UUID(tenant),f'customer-memory-erasure:{contact}:{fact_id}:{version}'))


def _identity_closure(cur,tenant,channel,identities):
    from tenant_connect_direct_identity import _aliases,direct_identity
    known=set()
    for identity in identities:
        if not direct_identity(identity):raise TenantLinearError('Ambiguous identity.',409)
        known.update(_aliases(identity))
    for _ in range(8):
        cur.execute('''SELECT identity_key,canonical_conversation_id FROM saas_connect_direct_identities
          WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid
            AND (identity_key=ANY(%s::text[]) OR canonical_conversation_id=ANY(%s::text[]))''',
          (tenant,channel,sorted(known),sorted(known)))
        expanded=set(known)
        for row in cur.fetchall():
            for identity in (str(row['identity_key']),str(row['canonical_conversation_id'])):
                if not direct_identity(identity):raise TenantLinearError('Ambiguous identity.',409)
                expanded.update(_aliases(identity))
        if len(expanded)>64:raise TenantLinearError('Ambiguous identity.',409)
        if expanded==known:return known
        known=expanded
    raise TenantLinearError('Ambiguous identity.',409)


def _lock_identities(cur,tenant,channel,identities):
    from tenant_connect_dialog_equivalence import lock as lock_identity
    before=_identity_closure(cur,tenant,channel,identities)
    lock_identity(cur,tenant,channel,sorted(before))
    after=_identity_closure(cur,tenant,channel,identities)
    if after!=before:raise TenantLinearError('Identity changed; retry.',409)
    return before


def _route(cur, tenant, policy, channel, conversation,*,prelocked=False):
    channel=identifier(channel)
    if not isinstance(conversation,str) or not re.fullmatch(r'[1-9][0-9]{5,22}@(s\.whatsapp\.net|c\.us|lid)',conversation):
        raise TenantLinearError('Ambiguous identity.',409)
    if not prelocked:_lock_identities(cur,tenant,channel,[conversation])
    # The erasure trigger takes the same channel gate without waiting. Keep it
    # until commit, so neither an existing link nor an in-flight operation can
    # outlive a successful contact erasure. Conservative until alias history
    # can be reconciled without fencing the whole channel.
    cur.execute('SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0)) AS acquired',
                ('customer-memory-channel:'+tenant+':'+channel,))
    if not cur.fetchone()['acquired']: raise TenantLinearError('Memory temporarily unavailable.',503)
    cur.execute('''SELECT 1 FROM saas_customer_memory_erased_channels WHERE tenant_id=%s::uuid
      AND channel_id=%s::uuid''',(tenant,channel))
    if cur.fetchone():raise TenantLinearError('Memory requires review.',409)
    route=assert_route(cur,tenant,policy,'connect',conversation,channel)
    # The operator history projection requires an existing channel and hides
    # merged source routes; a tenant-only route lookup is not sufficient.
    cur.execute('''SELECT name FROM saas_connect_channels WHERE tenant_id=%s::uuid
      AND connect_channel_id=%s::uuid''',(tenant,channel))
    channel_row=cur.fetchone()
    if not channel_row: raise TenantLinearError('Source unavailable.',404)
    route['memory_channel_name']=str(channel_row.get('name') or '')
    if route.get('conversation_kind') == 'group' or conflicted(cur,tenant,channel,conversation):
        raise TenantLinearError('Ambiguous identity.',409)
    return channel, route


def _subject(cur,tenant,contact,*,allow_archived=False):
    contact=identifier(contact)
    cur.execute('''SELECT id FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid
      AND id=%s::uuid AND (archived_at IS NULL OR %s)''',(tenant,contact,allow_archived))
    if not cur.fetchone(): raise TenantLinearError('Customer unavailable.',404)
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                ('customer-memory-subject:'+tenant+':'+contact,))
    return contact


def _erasure_pending(cur,tenant,contact):
    cur.execute('''SELECT 1 FROM saas_customer_memory_erasure_requests
      WHERE tenant_id=%s::uuid AND contact_id=%s::uuid AND state='pending' LIMIT 1''',(tenant,contact))
    return bool(cur.fetchone())


def _require_no_erasure_pending(cur,tenant,contact):
    if _erasure_pending(cur,tenant,contact):
        raise TenantLinearError('Memory erasure pending.',409)


def _lock_contact(cur,tenant,contact,*,allow_archived=False):
    # Call only after acquiring all relevant Connect identity locks.
    cur.execute('''SELECT 1 FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid
      AND id=%s::uuid AND (archived_at IS NULL OR %s) FOR SHARE''',(tenant,contact,allow_archived))
    if not cur.fetchone():raise TenantLinearError('Customer unavailable.',404)


def _subject_revision(cur,tenant,contact):
    cur.execute('''SELECT revision FROM saas_customer_memory_subject_revisions WHERE tenant_id=%s::uuid
      AND contact_id=%s::uuid''',(tenant,contact))
    row=cur.fetchone()
    if not row:raise TenantLinearError('Memory revision unavailable.',503)
    return row['revision']


def _verify_subject_revision(cur,tenant,contact,expected):
    cur.execute('''SELECT revision FROM saas_customer_memory_subject_revisions WHERE tenant_id=%s::uuid
      AND contact_id=%s::uuid FOR SHARE NOWAIT''',(tenant,contact))
    row=cur.fetchone()
    if not row or row['revision']!=expected:raise TenantLinearError('Memory changed; retry.',409)


def _bump_subject_revision(cur,tenant,contact,expected,*,erasure=False):
    cur.execute('''SELECT revision FROM saas_customer_memory_subject_revisions WHERE tenant_id=%s::uuid
      AND contact_id=%s::uuid FOR UPDATE NOWAIT''',(tenant,contact))
    row=cur.fetchone()
    if not row or row['revision']!=expected:raise TenantLinearError('Memory changed; retry.',409)
    cur.execute('''UPDATE saas_customer_memory_subject_revisions SET revision=revision+1,
      erasure_epoch=erasure_epoch+CASE WHEN %s THEN 1 ELSE 0 END
      WHERE tenant_id=%s::uuid AND contact_id=%s::uuid''',(erasure,tenant,contact))


def _crm_link_revision(cur,tenant,contact,channel,conversation):
    cur.execute('''SELECT contact_id FROM saas_crm_native_contact_conversations WHERE tenant_id=%s::uuid
      AND provider='connect' AND channel_id=%s::uuid AND conversation_id=%s FOR SHARE NOWAIT''',
      (tenant,channel,conversation))
    mapping=cur.fetchone()
    if mapping and str(mapping['contact_id'])!=contact:raise TenantLinearError('Ambiguous identity.',409)
    cur.execute('''SELECT revision FROM saas_customer_memory_crm_link_revisions WHERE tenant_id=%s::uuid
      AND provider='connect' AND channel_id=%s::uuid AND conversation_id=%s''',(tenant,channel,conversation))
    return int((cur.fetchone() or {}).get('revision') or 0), bool(mapping)


def _crm_revision_matches(saved,current,mapped):
    # An owner-attested previously unlinked route can acquire its first CRM
    # mapping to this same contact. Any later change, removal or mismatch fences.
    return saved==current or (saved==0 and current==1 and mapped)


def _source_token(tenant,channel,external):
    # Derive a domain- and tenant-specific key from the existing encryption
    # key. Never store the provider ID or the key in a tombstone.
    try:
        key=base64.urlsafe_b64decode(os.environ.get('TENANT_ENCRYPTION_KEY','').encode())
        if len(key)!=32:raise ValueError()
    except (ValueError,TypeError):raise TenantLinearError('Memory key unavailable.',503) from None
    key_id=hmac.new(key,b'customer-memory-key-version',hashlib.sha256).hexdigest()[:24]
    tenant_key=hmac.new(key,('customer-memory-source:'+tenant).encode(),hashlib.sha256).digest()
    token=hmac.new(tenant_key,json.dumps([channel,external],ensure_ascii=True).encode(),hashlib.sha256).hexdigest()
    return key_id,token


def _source_gate(tenant):return 'customer-memory-source-tenant:'+tenant


def _source_external_token(tenant,source):
    return _source_token(tenant,'customer-memory-external',source['external_id'])


def _source_read_guard(cur,tenant,source):
    """Fail closed if source erasure is in flight or already committed."""
    cur.execute("SELECT current_setting('transaction_isolation') AS isolation")
    if cur.fetchone()['isolation']!='read committed':
        raise TenantLinearError('Memory isolation unavailable.',503)
    key_id,token=_source_token(tenant,source['channel'],source['external_id'])
    external_key,external_token=_source_external_token(tenant,source)
    if external_key!=key_id:raise TenantLinearError('Memory key changed.',503)
    cur.execute('SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0)) AS ok',(_source_gate(tenant),))
    if not cur.fetchone()['ok']:raise TenantLinearError('Memory temporarily unavailable.',503)
    cur.execute('''SELECT 1 FROM saas_customer_memory_erased_sources WHERE tenant_id=%s::uuid
      AND (token_key_id<>%s OR source_token='' OR external_token='' OR source_token IN (%s,%s)
           OR external_token IN (%s,%s))
      UNION ALL SELECT 1 FROM saas_customer_memory_source_erasure_events WHERE tenant_id=%s::uuid
      AND (token_key_id<>%s OR source_token='' OR source_token IN (%s,%s)) LIMIT 1''',
      (tenant,key_id,token,external_token,token,external_token,tenant,key_id,token,external_token))
    if cur.fetchone():raise TenantLinearError('Source suppressed.',409)
    return key_id,token


def _lock_sources_for_erasure(cur,tenant,pairs):
    # This is deliberately tenant-wide while mixed/legacy key generations may
    # exist. A per-token lock alone lets concurrent key rotations bypass it.
    # No model/network call may execute while this transaction is open.
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(_source_gate(tenant),))


def _route_token(tenant,channel,identity):
    # Collapse provider-equivalent telephone JID spellings only for conservative
    # conflict/erasure fencing; this never confirms a customer's identity.
    from tenant_connect_direct_identity import phone_jid
    return _source_token(tenant,'customer-memory-route:'+channel,phone_jid(identity) or identity)


def _claim_token(tenant,value):
    key_id,token=_source_token(tenant,'customer-memory-claim',unicodedata.normalize('NFKC',value.strip()).casefold())
    return key_id,token


def _erasure_cutoff(cur,tenant,contact):
    cur.execute('''SELECT max(created_at) AS cutoff FROM saas_customer_memory_claim_suppressions
      WHERE tenant_id=%s::uuid AND contact_id=%s::uuid''',(tenant,contact))
    return cur.fetchone()['cutoff']


def _assert_claim_allowed(cur,tenant,contact,value,key_id,source):
    claim_key,_=_claim_token(tenant,value)
    if claim_key!=key_id:raise TenantLinearError('Memory key changed.',409)
    # A deleted claim has no content oracle. Only evidence received AND dated
    # after the subject fence is a new basis; old/undated evidence remains held.
    cutoff=_erasure_cutoff(cur,tenant,contact)
    if cutoff:
        cur.execute('''SELECT g.erasure_epoch AS observed,r.erasure_epoch AS current
          FROM saas_customer_memory_source_generations g JOIN saas_customer_memory_subject_revisions r
            ON r.tenant_id=g.tenant_id AND r.contact_id=g.contact_id
          WHERE g.tenant_id=%s::uuid AND g.contact_id=%s::uuid AND g.source_message_id=%s::uuid''',
          (tenant,contact,str(source['id'])))
        generation=cur.fetchone()
        if (not generation or generation['observed']!=generation['current'] or not source.get('happened_at')
                or source['happened_at']<=cutoff):
            raise TenantLinearError('Memory requires new evidence.',409)


def _source_kind_supported(source,kind):
    """Require evidence of the asserted author; client supplied labels do not count."""
    raw=source.get('raw')
    if not isinstance(raw,dict):return False
    if (raw.get('ai_authored') is not None and raw.get('ai_authored') is not False
            or raw.get('run_id')
            or raw.get('authorshipUncertain') is not None and raw.get('authorshipUncertain') is not False
            or raw.get('conversationIdentityConflict') is not None and raw.get('conversationIdentityConflict') is not False
            or raw.get('pilot_test_role_override') is not None
            or str(source.get('author_name') or '').strip().casefold()=='ai agent'):
        return False
    employee=raw.get('verified_employee_id')
    if kind=='customer_statement':
        return (source.get('direction')=='incoming' and employee is None
                and (raw.get('fromMe') is None or raw.get('fromMe') is False))
    if kind=='employee_observation':
        return type(employee) is int and employee>0 and source.get('direction') in ('incoming','outgoing')
    return False


def _source(cur,tenant,link,source_id):
    source_id=identifier(source_id)
    try:
        cur.execute('''SELECT id,external_id,channel,body,message_type,direction,author_name,happened_at,created_at,raw,xmin::text AS row_revision FROM saas_crm_messages
          WHERE tenant_id=%s::uuid AND id=%s::uuid AND kommo_lead_id=%s AND channel=%s FOR SHARE NOWAIT''',
          (tenant,source_id,link['lead_id'],'connect:'+str(link['channel_id'])))
    except psycopg.errors.LockNotAvailable as exc:
        raise TenantLinearError('Memory temporarily unavailable.',503) from exc
    row=cur.fetchone()
    if (not row or row['message_type'] != 'text' or not row['body']
            or not str(row['external_id']).startswith('connect:') or row['external_id']=='connect:'):
        raise TenantLinearError('Source unavailable.',404)
    payload={k:row.get(k) for k in ('body','message_type','direction','author_name','happened_at','raw','row_revision')}
    version=hashlib.sha256(json.dumps(payload,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()
    return row,version


def _link(cur,tenant,policy,contact,link_id,*,lock='SHARE',prelocked=False):
    link_id=identifier(link_id)
    cur.execute('''SELECT * FROM saas_customer_memory_links WHERE tenant_id=%s::uuid AND contact_id=%s::uuid
      AND id=%s::uuid''',(tenant,contact,link_id))
    prior=cur.fetchone()
    if not prior or prior['state']!='confirmed': raise TenantLinearError('Source unavailable.',404)
    _,route=_route(cur,tenant,policy,str(prior['channel_id']),prior['conversation_id'],prelocked=prelocked)
    _lock_contact(cur,tenant,contact)
    crm_revision,crm_mapped=_crm_link_revision(cur,tenant,contact,str(prior['channel_id']),prior['conversation_id'])
    channel=str(prior['channel_id']);aliases=_identity_closure(cur,tenant,channel,[prior['conversation_id']])
    current_key,_=_source_token(tenant,'customer-memory-subject',contact)
    tokens=sorted({_route_token(tenant,channel,str(alias))[1] for alias in aliases})
    cur.execute('''SELECT 1 FROM saas_customer_memory_links WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
      AND state='confirmed' AND id<>%s::uuid
      AND (route_token='' OR subject_key_id<>%s OR route_token=ANY(%s::text[])) LIMIT 1''',
      (tenant,channel,link_id,current_key,tokens))
    if cur.fetchone() or prior['subject_key_id']!=current_key:raise TenantLinearError('Ambiguous identity.',409)
    cur.execute('''SELECT * FROM saas_customer_memory_links WHERE tenant_id=%s::uuid AND contact_id=%s::uuid
      AND id=%s::uuid FOR ''' + ('UPDATE NOWAIT' if lock=='UPDATE' else 'SHARE NOWAIT'),(tenant,contact,link_id))
    link=cur.fetchone()
    if (not link or link['state']!='confirmed' or link['version']!=prior['version']
            or str(link['channel_id'])!=str(prior['channel_id']) or link['conversation_id']!=prior['conversation_id']
            or int(route['lead_id'])!=int(link['lead_id']) or not _crm_revision_matches(link['crm_link_revision'],crm_revision,crm_mapped)):
        raise TenantLinearError('Source unavailable.',404)
    link=dict(link)
    link['display_name']=str(route.get('manual_display_name') or route.get('contact_name') or route.get('display_name') or '')
    link['channel_name']=route['memory_channel_name']
    return link


def confirm_link(profile, contact, channel, conversation, confirm):
    if confirm is not True: raise TenantLinearError('Confirmation required.',400)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=_guard(cur,profile,'update')
            if policy.profile.get('role')!='owner': raise TenantLinearError('Owner required.',403)
            contact=_subject(cur,tenant,contact)
            _require_no_erasure_pending(cur,tenant,contact)
            subject_key_id,subject_token=_source_token(tenant,'customer-memory-subject',contact)
            cur.execute('''SELECT 1 FROM saas_customer_memory_erased_subjects WHERE tenant_id=%s::uuid
              AND (token_key_id<>%s OR subject_token='' OR subject_token=%s) LIMIT 1''',
              (tenant,subject_key_id,subject_token))
            if cur.fetchone():raise TenantLinearError('Memory requires review.',409)
            channel,route=_route(cur,tenant,policy,channel,conversation)
            # The same locked provider identity closure fences every currently
            # verified alias, without treating a shared phone as identity proof.
            aliases=_identity_closure(cur,tenant,channel,[conversation])
            route_token=_route_token(tenant,channel,conversation)[1]
            candidate_tokens=sorted({_route_token(tenant,channel,str(alias))[1] for alias in aliases})
            cur.execute('''SELECT 1 FROM saas_customer_memory_erased_routes WHERE tenant_id=%s::uuid
              AND (token_key_id<>%s OR route_token='' OR route_token=ANY(%s::text[])) LIMIT 1''',
              (tenant,subject_key_id,candidate_tokens))
            if cur.fetchone():raise TenantLinearError('Memory requires review.',409)
            _lock_contact(cur,tenant,contact)
            cur.execute('''SELECT contact_id,conversation_id FROM saas_customer_memory_links
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND state='confirmed'
                AND (route_token='' OR subject_key_id<>%s OR route_token=ANY(%s::text[]))''',
              (tenant,channel,subject_key_id,candidate_tokens))
            existing_routes=cur.fetchall()
            if any(str(r['contact_id'])!=contact or r['conversation_id']!=conversation for r in existing_routes):
                raise TenantLinearError('Ambiguous identity.',409)
            crm_revision,crm_mapped=_crm_link_revision(cur,tenant,contact,channel,conversation)
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('customer-memory-link:'+tenant+':'+channel+':'+conversation,))
            cur.execute('''SELECT id,contact_id,crm_link_revision FROM saas_customer_memory_links WHERE tenant_id=%s::uuid
              AND channel_id=%s::uuid AND conversation_id=%s AND state='confirmed' FOR UPDATE''',(tenant,channel,conversation))
            old=cur.fetchone()
            if old:
                if str(old['contact_id']) != contact or not _crm_revision_matches(old['crm_link_revision'],crm_revision,crm_mapped): raise TenantLinearError('Ambiguous identity.',409)
                return {'id':str(old['id'])}
            cur.execute('''INSERT INTO saas_customer_memory_subject_revisions(tenant_id,contact_id,revision)
              VALUES(%s::uuid,%s::uuid,1) ON CONFLICT DO NOTHING''',(tenant,contact))
            link=str(uuid.uuid4())
            cur.execute('''INSERT INTO saas_customer_memory_links
              (id,tenant_id,contact_id,channel_id,conversation_id,lead_id,crm_link_revision,subject_token,subject_key_id,route_token,state,confirmed_by)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,'confirmed',%s)''',
              (link,tenant,contact,channel,conversation,route['lead_id'],crm_revision,subject_token,subject_key_id,route_token,actor))
            cur.execute('''UPDATE saas_customer_memory_subject_revisions SET revision=revision+1
              WHERE tenant_id=%s::uuid AND contact_id=%s::uuid''',(tenant,contact))
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
            cur.execute('''UPDATE saas_customer_memory_subject_revisions SET revision=revision+1
              WHERE tenant_id=%s::uuid AND contact_id=%s::uuid''',(tenant,contact))
            _audit(cur,tenant,actor,'customer_memory_link_revoked','customer_memory_link',link_id)
        conn.commit()
    return {'revoked':True}


def add_fact(profile,contact,data):
    required={'request_id','link_id','source_message_id','category','value','source_kind','reliability','evidence_quote','evidence_confirmed'}
    if (not isinstance(data,dict) or not required<=set(data)
            or set(data)-required-{'group_fact_id','group_version','distinct_confirmation'}
            or (('group_fact_id' in data)!=('group_version' in data))
            or ('distinct_confirmation' in data and data['distinct_confirmation'] is not True)
            or ('group_fact_id' in data and 'distinct_confirmation' in data)):
        raise TenantLinearError('Invalid fact.',400)
    if data['category'] not in _CATEGORIES or data['source_kind'] not in _SOURCE_KINDS or data['reliability'] not in _STATUSES:
        raise TenantLinearError('Invalid fact.',400)
    value=_text(data['value']); quote=_text(data['evidence_quote']); request_id=identifier(data['request_id'])
    if len(quote)<4 or len(quote)>200 or data['evidence_confirmed'] is not True:
        raise TenantLinearError('Evidence confirmation required.',400)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout='150ms'")
            tenant,actor,policy=_guard(cur,profile,'update'); contact=_subject(cur,tenant,contact)
            _require_no_erasure_pending(cur,tenant,contact)
            subject_revision=_subject_revision(cur,tenant,contact)
            # Client request identifiers never address another tenant's primary key.
            fact=str(uuid.uuid5(uuid.UUID(tenant),request_id))
            _lock_fact_ids(cur,tenant,[fact])
            cur.execute('''SELECT 1 FROM saas_customer_memory_erased_fact_ids
              WHERE tenant_id=%s::uuid AND fact_id=%s::uuid''',(tenant,fact))
            if cur.fetchone():raise TenantLinearError('Fact identifier suppressed.',409)
            group_id=fact
            group_source=None;existing_group=None
            if 'group_fact_id' in data:
                if type(data['group_version']) is not int:raise TenantLinearError('Invalid version.',400)
                group_fact=identifier(data['group_fact_id'])
                cur.execute('''SELECT f.*,l.channel_id,l.conversation_id FROM saas_customer_memory_facts f
                  JOIN saas_customer_memory_links l ON l.tenant_id=f.tenant_id AND l.id=f.link_id
                  WHERE f.tenant_id=%s::uuid AND f.contact_id=%s::uuid AND f.id=%s::uuid''',(tenant,contact,group_fact))
                existing_group=cur.fetchone()
                if not existing_group or existing_group['version']!=data['group_version'] or not existing_group['claim_group_id']:
                    raise TenantLinearError('Fact unavailable.',409)
                cur.execute('''SELECT channel_id,conversation_id FROM saas_customer_memory_links
                  WHERE tenant_id=%s::uuid AND contact_id=%s::uuid AND id=%s::uuid AND state='confirmed' ''',
                  (tenant,contact,identifier(data['link_id'])))
                target=cur.fetchone()
                if not target:raise TenantLinearError('Source unavailable.',404)
                routes=[existing_group,target]
                for chan in sorted({str(r['channel_id']) for r in routes}):
                    _lock_identities(cur,tenant,chan,sorted({r['conversation_id'] for r in routes if str(r['channel_id'])==chan}))
                _lock_contact(cur,tenant,contact)
                group_link=_link(cur,tenant,policy,contact,str(existing_group['link_id']),prelocked=True)
                group_source,group_source_version=_source(cur,tenant,group_link,str(existing_group['source_message_id']))
                if (group_source_version!=existing_group['source_version'] or not _source_kind_supported(group_source,existing_group['source_kind'])
                        or existing_group['value']!=value or existing_group['category']!=data['category']):
                    raise TenantLinearError('Fact changed.',409)
                group_id=str(existing_group['claim_group_id'])
                link=_link(cur,tenant,policy,contact,data['link_id'],lock='UPDATE',prelocked=True)
            else:
                link=_link(cur,tenant,policy,contact,data['link_id'],lock='UPDATE')
            source,source_version=_source(cur,tenant,link,data['source_message_id'])
            if quote not in source['body']:raise TenantLinearError('Quote is not in source.',400)
            if group_source:
                group_key,group_token=_source_read_guard(cur,tenant,group_source)
                _,group_external=_source_external_token(tenant,group_source)
                if (group_key!=existing_group['token_key_id'] or group_token!=existing_group['source_token']
                        or group_external!=existing_group['external_token']):
                    raise TenantLinearError('Source changed.',409)
            key_id,token=_source_read_guard(cur,tenant,source)
            _,external_token=_source_external_token(tenant,source)
            _,quote_token=_source_token(tenant,'customer-memory-quote',quote)
            _assert_claim_allowed(cur,tenant,contact,value,key_id,source)
            _,claim_token=_claim_token(tenant,value)
            if not _source_kind_supported(source,data['source_kind']):
                raise TenantLinearError('Source direction mismatch.',400)
            # A key rotation cannot make existing facts or suppressions
            # incomparable. Require migration before any new fact.
            cur.execute('''SELECT 1 FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
              AND contact_id=%s::uuid AND token_key_id<>%s LIMIT 1''',(tenant,contact,key_id))
            if cur.fetchone():raise TenantLinearError('Memory key changed.',409)
            cur.execute('''SELECT 1 FROM saas_customer_memory_suppressions WHERE tenant_id=%s::uuid
              AND contact_id=%s::uuid AND (token_key_id<>%s OR source_message_id=%s::uuid OR source_token=%s)''',
              (tenant,contact,key_id,str(source['id']),token))
            if cur.fetchone(): raise TenantLinearError('Source suppressed.',409)
            cur.execute('''SELECT id,version,category,value,source_kind,reliability,link_id,source_message_id,
              source_version,source_token,external_token,token_key_id,evidence_quote_token FROM saas_customer_memory_facts
              WHERE tenant_id=%s::uuid AND id=%s::uuid''',(tenant,fact))
            existing=cur.fetchone()
            if existing:
                if (str(existing['link_id']) != str(link['id']) or str(existing['source_message_id']) != str(source['id'])
                        or existing['source_version']!=source_version or existing['source_token']!=token
                        or existing['external_token']!=external_token or existing['token_key_id']!=key_id
                        or existing['value'] != value or existing['evidence_quote_token']!=quote_token
                        or any(existing[k] != data[k] for k in ('category','source_kind','reliability'))):
                    raise TenantLinearError('Request conflict.',409)
                _verify_subject_revision(cur,tenant,contact,subject_revision)
                return {'id':fact,'version':existing['version']}
            cur.execute('''SELECT 1 FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
              AND contact_id=%s::uuid AND source_message_id=%s::uuid AND category=%s AND lower(value)=lower(%s)''',
              (tenant,contact,str(source['id']),data['category'],value))
            if cur.fetchone() and data.get('distinct_confirmation') is not True:
                raise TenantLinearError('Fact already recorded; clarify whether distinct.',409)
            cur.execute('''SELECT count(*) AS n FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
              AND link_id=%s::uuid''',(tenant,str(link['id'])))
            if cur.fetchone()['n']>=200: raise TenantLinearError('Memory limit reached.',409)
            cur.execute('''INSERT INTO saas_customer_memory_facts(id,tenant_id,contact_id,link_id,link_version,
              source_message_id,source_version,source_token,external_token,token_key_id,claim_token,claim_group_id,evidence_quote_token,category,value,source_kind,reliability,event_at,last_confirmed_at)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,
                CASE WHEN %s='confirmed' THEN now() END)''',
              (fact,tenant,contact,str(link['id']),link['version'],str(source['id']),source_version,token,external_token,key_id,claim_token,group_id,quote_token,
               data['category'],value,data['source_kind'],data['reliability'],source['happened_at'],data['reliability']))
            cur.execute('''INSERT INTO saas_customer_memory_fact_versions(fact_id,version,value,reliability,actor,claim_token,evidence_quote_token)
              VALUES(%s::uuid,1,%s,%s,%s,%s,%s)''',(fact,value,data['reliability'],actor,claim_token,quote_token))
            _audit(cur,tenant,actor,'customer_memory_fact_added','customer_memory_fact',fact)
            _bump_subject_revision(cur,tenant,contact,subject_revision)
        conn.commit()
    return {'id':fact,'version':1}


def read(profile,contact):
    """Read only facts whose source, route, identity and current rights pass."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            # Temporary conservative bound; measure production latency before rollout.
            deadline=time.monotonic()+0.5
            cur.execute("SET LOCAL lock_timeout='150ms'")
            cur.execute("SET LOCAL statement_timeout='500ms'")
            tenant,_,policy=_guard(cur,profile,'view');contact=_subject(cur,tenant,contact)
            # Without proven claim equivalence, do not expose unrelated-looking
            # duplicates while erasure remains unresolved.
            if _erasure_pending(cur,tenant,contact):return {'links':[],'facts':[],'incomplete':True}
            cur.execute('''SELECT id,channel_id,conversation_id FROM saas_customer_memory_links WHERE tenant_id=%s::uuid
              AND contact_id=%s::uuid AND state='confirmed' ORDER BY channel_id,conversation_id LIMIT 100''',(tenant,contact))
            candidates=cur.fetchall(); links=[]; facts=[]; scanned=0
            cur.execute('''SELECT revision FROM saas_customer_memory_subject_revisions WHERE tenant_id=%s::uuid
              AND contact_id=%s::uuid''',(tenant,contact))
            subject_revision=cur.fetchone()
            if candidates and not subject_revision:raise TenantLinearError('Memory revision unavailable.',503)
            cutoff=_erasure_cutoff(cur,tenant,contact)
            # The merge path locks all aliases in sorted order. Acquire the same
            # complete order before taking the CRM contact row lock.
            for channel in sorted({str(x['channel_id']) for x in candidates}):
                if time.monotonic()>deadline:raise TenantLinearError('Memory read timed out.',503)
                identities=sorted(x['conversation_id'] for x in candidates if str(x['channel_id'])==channel)
                _lock_identities(cur,tenant,channel,identities)
            _lock_contact(cur,tenant,contact)
            for candidate in candidates:
                if time.monotonic()>deadline:raise TenantLinearError('Memory read timed out.',503)
                try:
                    link=_link(cur,tenant,policy,contact,str(candidate['id']),prelocked=True)
                except TenantLinearError as exc:
                    if exc.status == 503: raise
                    continue
                if len(links) >= 50: break
                links.append({'id':str(link['id']),'version':link['version'],
                              'channel_id':str(link['channel_id']),'conversation_id':link['conversation_id'],
                              'lead_id':link['lead_id'],'display_name':link['display_name'],'channel_name':link['channel_name']})
                if scanned >= 100: continue
                cur.execute('''SELECT * FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
                  AND contact_id=%s::uuid AND link_id=%s::uuid ORDER BY updated_at DESC,id LIMIT 50''',
                  (tenant,contact,str(link['id'])))
                for fact in cur.fetchall():
                    if len(facts) >= 50 or scanned >= 100: break
                    if time.monotonic()>deadline:raise TenantLinearError('Memory read timed out.',503)
                    scanned+=1
                    if fact['link_version']!=link['version'] or not fact['claim_token'] or not fact['token_key_id'] or not fact['evidence_quote_token']: continue
                    if not fact['claim_group_id']:continue
                    cur.execute('''SELECT 1 FROM saas_customer_memory_group_fences WHERE tenant_id=%s::uuid
                      AND contact_id=%s::uuid AND claim_group_id=%s::uuid''',
                      (tenant,contact,str(fact['claim_group_id'])))
                    if cur.fetchone(): continue
                    try:
                        source,version=_source(cur,tenant,link,str(fact['source_message_id']))
                        key_id,source_token=_source_read_guard(cur,tenant,source)
                    except TenantLinearError as exc:
                        if exc.status==503:raise
                        continue
                    _,external_token=_source_external_token(tenant,source)
                    if (key_id!=fact['token_key_id'] or source_token!=fact['source_token']
                            or external_token!=fact['external_token']):continue
                    if version!=fact['source_version'] or not _source_kind_supported(source,fact['source_kind']):continue
                    if cutoff:
                        try:_assert_claim_allowed(cur,tenant,contact,fact['value'],fact['token_key_id'],source)
                        except TenantLinearError:continue
                    facts.append({k:fact[k] for k in ('category','value','source_kind','reliability','event_at',
                      'observed_at','valid_from','valid_to','last_confirmed_at','version','updated_at')})
                    facts[-1].update(id=str(fact['id']),group_id=str(fact['claim_group_id']),link_id=str(link['id']),
                                     source_message_id=str(fact['source_message_id']))
            facts.sort(key=lambda x:(x['updated_at'],x['id']),reverse=True)
            # Bound the output; never return counts or versions from hidden sources.
            if time.monotonic()>deadline:raise TenantLinearError('Memory read timed out.',503)
            if subject_revision:
                cur.execute('''SELECT revision FROM saas_customer_memory_subject_revisions WHERE tenant_id=%s::uuid
                  AND contact_id=%s::uuid FOR SHARE NOWAIT''',(tenant,contact))
                final_revision=cur.fetchone()
                if not final_revision or final_revision['revision']!=subject_revision['revision']:
                    raise TenantLinearError('Memory changed; retry.',409)
            # Constant partial marker does not reveal hidden-source existence.
            return {'links':links[:50],'facts':facts[:50],'incomplete':True}


def change_fact(profile,contact,fact_id,expected_version,action,*,value=None,evidence_quote=None,evidence_confirmed=None):
    if type(expected_version) is not int or expected_version<1 or action not in {'edit','confirm','outdated','refute','delete'}:
        raise TenantLinearError('Invalid fact change.',400)
    if action=='edit':
        value=_text(value);evidence_quote=_text(evidence_quote)
        if not 4<=len(evidence_quote)<=200 or evidence_confirmed is not True:
            raise TenantLinearError('Evidence confirmation required.',400)
    elif value is not None or evidence_quote is not None or evidence_confirmed is not None:
        raise TenantLinearError('Invalid fact change.',400)
    with _connect() as conn:
        # Erasure recovery must never install migrations as a side effect of an
        # OFF-flag request. The coordinator must already exist.
        if action!='delete':_ensure_schema(conn)
        with conn.cursor() as cur:
            # An unresponsive memory operation must not leave a manual erasure
            # request waiting forever. A timeout rolls back; it is not accepted.
            cur.execute("SET LOCAL lock_timeout='2s'")
            permission='delete' if action=='delete' else 'update'
            tenant,actor,policy=_guard(cur,profile,permission,erasure_recovery=action=='delete')
            if action=='delete':
                cur.execute("SELECT to_regclass('saas_customer_memory_erasure_requests') AS relation")
                if not cur.fetchone()['relation']:raise TenantLinearError('Erasure unavailable.',404)
            owner_delete=action=='delete' and policy.profile.get('role')=='owner'
            contact=identifier(contact);fact_id=identifier(fact_id)
            request_id=_erasure_request_id(tenant,contact,fact_id,expected_version) if owner_delete else None
            if owner_delete:
                cur.execute('''SELECT state FROM saas_customer_memory_erasure_requests WHERE id=%s::uuid
                  AND tenant_id=%s::uuid AND contact_id=%s::uuid''',(request_id,tenant,contact))
                existing_request=cur.fetchone()
                if existing_request:return {'request_id':request_id,'status':existing_request['state']}
            contact=_subject(cur,tenant,contact,allow_archived=owner_delete)
            if not owner_delete:_require_no_erasure_pending(cur,tenant,contact)
            subject_revision=_subject_revision(cur,tenant,contact)
            if owner_delete:
                # The subject lock serializes same-contact retries; recheck after
                # waiting so a committed prior request remains idempotent.
                cur.execute('''SELECT state FROM saas_customer_memory_erasure_requests WHERE id=%s::uuid
                  AND tenant_id=%s::uuid AND contact_id=%s::uuid''',(request_id,tenant,contact))
                existing_request=cur.fetchone()
                if existing_request:return {'request_id':request_id,'status':existing_request['state']}
            cur.execute('''SELECT link_id,source_message_id,source_version,source_kind,category,value,claim_token,source_token,external_token,token_key_id,claim_group_id FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
              AND contact_id=%s::uuid AND id=%s::uuid''',(tenant,contact,fact_id))
            ref=cur.fetchone()
            if not ref:raise TenantLinearError('Fact unavailable.',404)
            if owner_delete:
                cur.execute('''SELECT id FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid AND contact_id=%s::uuid
                  AND ((%s::uuid IS NOT NULL AND claim_group_id=%s::uuid) OR (%s::uuid IS NULL AND id=%s::uuid))''',
                  (tenant,contact,ref['claim_group_id'],ref['claim_group_id'],ref['claim_group_id'],fact_id))
                _lock_fact_ids(cur,tenant,[str(row['id']) for row in cur.fetchall()])
                _lock_contact(cur,tenant,contact,allow_archived=True)
                cur.execute('''SELECT * FROM saas_customer_memory_links WHERE tenant_id=%s::uuid
                  AND contact_id=%s::uuid AND id=%s::uuid FOR UPDATE''',(tenant,contact,str(ref['link_id'])))
                link=cur.fetchone()
                if not link: raise TenantLinearError('Fact unavailable.',404)
                # Lock all implicated source messages before locking any fact;
                # source-deletion cleanup uses message -> fact order.
                cur.execute('''SELECT DISTINCT f.source_message_id FROM saas_customer_memory_facts f
                  LEFT JOIN saas_customer_memory_fact_versions v ON v.fact_id=f.id
                  WHERE f.tenant_id=%s::uuid AND f.contact_id=%s::uuid
                    AND ((%s::uuid IS NOT NULL AND f.claim_group_id=%s::uuid) OR (%s::uuid IS NULL AND f.id=%s::uuid))''',
                  (tenant,contact,ref['claim_group_id'],ref['claim_group_id'],ref['claim_group_id'],fact_id))
                source_ids=[str(x['source_message_id']) for x in cur.fetchall()]
                cur.execute('''SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid
                  AND id=ANY(%s::uuid[]) ORDER BY id FOR SHARE NOWAIT''',(tenant,source_ids))
                cur.fetchall()
                cur.execute('''SELECT DISTINCT token_key_id,source_token FROM saas_customer_memory_facts
                  WHERE tenant_id=%s::uuid AND contact_id=%s::uuid
                  AND ((%s::uuid IS NOT NULL AND claim_group_id=%s::uuid) OR (%s::uuid IS NULL AND id=%s::uuid))''',
                  (tenant,contact,ref['claim_group_id'],ref['claim_group_id'],ref['claim_group_id'],fact_id))
                _lock_sources_for_erasure(cur,tenant,[(row['token_key_id'],row['source_token']) for row in cur.fetchall()])
            else:
                link=_link(cur,tenant,policy,contact,str(ref['link_id']),lock='UPDATE')
            if not owner_delete:
                # Source delete takes the message lock before its cleanup trigger
                # locks facts. Use the same order to avoid a deletion/edit cycle.
                source,source_version=_source(cur,tenant,link,str(ref['source_message_id']))
                source_key,source_token=_source_read_guard(cur,tenant,source)
                _,external_token=_source_external_token(tenant,source)
                if (source_key!=ref['token_key_id'] or source_token!=ref['source_token']
                        or external_token!=ref['external_token']):
                    raise TenantLinearError('Source changed.',409)
                if source_version!=ref['source_version'] or not _source_kind_supported(source,ref['source_kind']):raise TenantLinearError('Source changed.',409)
                if action=='edit' and evidence_quote not in source['body']:
                    raise TenantLinearError('Quote is not in source.',400)
            cur.execute('''SELECT * FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
              AND contact_id=%s::uuid AND id=%s::uuid FOR UPDATE''',(tenant,contact,fact_id))
            fact=cur.fetchone()
            if not fact or fact['version']!=expected_version or (not owner_delete and fact['link_version']!=link['version']):
                raise TenantLinearError('Fact changed.',409)
            if not owner_delete and (fact['source_version']!=source_version or str(fact['source_message_id'])!=str(ref['source_message_id'])):
                raise TenantLinearError('Source changed.',409)
            if action!='delete':
                key_id,_=_source_token(tenant,'','')
                _assert_claim_allowed(cur,tenant,contact,value if action=='edit' else fact['value'],key_id,source)
                cur.execute('''SELECT 1 FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
                  AND contact_id=%s::uuid AND token_key_id<>%s LIMIT 1''',(tenant,contact,key_id))
                if cur.fetchone():raise TenantLinearError('Memory key changed.',409)
                cur.execute('''SELECT 1 FROM saas_customer_memory_suppressions WHERE tenant_id=%s::uuid
                  AND contact_id=%s::uuid AND token_key_id<>%s LIMIT 1''',(tenant,contact,key_id))
                if cur.fetchone():raise TenantLinearError('Memory key changed.',409)
                cur.execute('''SELECT 1 FROM saas_customer_memory_suppressions WHERE tenant_id=%s::uuid
                  AND contact_id=%s::uuid AND source_message_id=%s::uuid''',
                  (tenant,contact,str(fact['source_message_id'])))
                if cur.fetchone():raise TenantLinearError('Source suppressed.',409)
            if action=='delete':
                # Only the owner may erase equal values across source routes.
                # Suppress every known source without retaining the value/hash.
                if not owner_delete:raise TenantLinearError('Owner required.',403)
                cur.execute('''INSERT INTO saas_customer_memory_erased_fact_ids(tenant_id,fact_id)
                  SELECT tenant_id,id FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid AND contact_id=%s::uuid
                    AND ((%s::uuid IS NOT NULL AND claim_group_id=%s::uuid) OR (%s::uuid IS NULL AND id=%s::uuid))
                  ON CONFLICT DO NOTHING''',
                  (tenant,contact,fact['claim_group_id'],fact['claim_group_id'],fact['claim_group_id'],fact_id))
                cur.execute('''INSERT INTO saas_customer_memory_source_erasure_events(tenant_id,source_token,token_key_id)
                  SELECT DISTINCT tenant_id,source_token,token_key_id FROM saas_customer_memory_facts
                  WHERE tenant_id=%s::uuid AND contact_id=%s::uuid
                    AND ((%s::uuid IS NOT NULL AND claim_group_id=%s::uuid) OR (%s::uuid IS NULL AND id=%s::uuid))''',
                  (tenant,contact,fact['claim_group_id'],fact['claim_group_id'],fact['claim_group_id'],fact_id))
                cur.execute('''INSERT INTO saas_customer_memory_source_erasure_events(tenant_id,source_token,token_key_id)
                  SELECT DISTINCT tenant_id,external_token,token_key_id FROM saas_customer_memory_facts
                  WHERE tenant_id=%s::uuid AND contact_id=%s::uuid
                    AND ((%s::uuid IS NOT NULL AND claim_group_id=%s::uuid) OR (%s::uuid IS NULL AND id=%s::uuid))''',
                  (tenant,contact,fact['claim_group_id'],fact['claim_group_id'],fact['claim_group_id'],fact_id))
                if fact['claim_group_id']:
                    cur.execute('''INSERT INTO saas_customer_memory_group_fences(tenant_id,contact_id,claim_group_id)
                      VALUES(%s::uuid,%s::uuid,%s::uuid) ON CONFLICT DO NOTHING''',
                      (tenant,contact,str(fact['claim_group_id'])))
                claim_key,claim='',''  # subject-wide write fence; no deleted value retained
                cur.execute('''INSERT INTO saas_customer_memory_claim_suppressions(tenant_id,contact_id,claim_token,token_key_id)
                  VALUES(%s::uuid,%s::uuid,%s,%s) ON CONFLICT(tenant_id,contact_id,claim_token)
                  DO UPDATE SET created_at=clock_timestamp()''',(tenant,contact,claim,claim_key))
                if not fact['claim_group_id']:
                    # A legacy value without a stable group cannot be compared
                    # safely: delete only the selected row and fence the subject.
                    cur.execute('''INSERT INTO saas_customer_memory_suppressions
                      (tenant_id,contact_id,source_message_id,source_token,external_token,token_key_id)
                      VALUES(%s::uuid,%s::uuid,%s::uuid,'','','') ON CONFLICT DO NOTHING''',
                      (tenant,contact,str(fact['source_message_id'])))
                    cur.execute('DELETE FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,fact_id))
                else:
                    cur.execute('''INSERT INTO saas_customer_memory_suppressions(tenant_id,contact_id,source_message_id,source_token,external_token,token_key_id)
                      SELECT f.tenant_id,f.contact_id,f.source_message_id,
                        CASE WHEN count(DISTINCT f.source_token)=1 AND count(DISTINCT f.token_key_id)=1 THEN max(f.source_token) ELSE '' END,
                        CASE WHEN count(DISTINCT f.external_token)=1 AND count(DISTINCT f.token_key_id)=1 THEN max(f.external_token) ELSE '' END,
                        CASE WHEN count(DISTINCT f.source_token)=1 AND count(DISTINCT f.token_key_id)=1 THEN max(f.token_key_id) ELSE '' END
                        FROM saas_customer_memory_facts f
                      LEFT JOIN saas_customer_memory_fact_versions v ON v.fact_id=f.id
                      WHERE f.tenant_id=%s::uuid AND f.contact_id=%s::uuid
                        AND f.claim_group_id=%s::uuid
                        GROUP BY f.tenant_id,f.contact_id,f.source_message_id
                        ON CONFLICT(tenant_id,contact_id,source_message_id) DO UPDATE SET
                          source_token=CASE WHEN saas_customer_memory_suppressions.source_token=EXCLUDED.source_token AND saas_customer_memory_suppressions.token_key_id=EXCLUDED.token_key_id THEN EXCLUDED.source_token ELSE '' END,
                          external_token=CASE WHEN saas_customer_memory_suppressions.external_token=EXCLUDED.external_token AND saas_customer_memory_suppressions.token_key_id=EXCLUDED.token_key_id THEN EXCLUDED.external_token ELSE '' END,
                          token_key_id=CASE WHEN saas_customer_memory_suppressions.source_token=EXCLUDED.source_token AND saas_customer_memory_suppressions.external_token=EXCLUDED.external_token AND saas_customer_memory_suppressions.token_key_id=EXCLUDED.token_key_id THEN EXCLUDED.token_key_id ELSE '' END''',
                      (tenant,contact,str(fact['claim_group_id'])))
                    cur.execute('''DELETE FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
                      AND contact_id=%s::uuid AND claim_group_id=%s::uuid''',
                      (tenant,contact,str(fact['claim_group_id'])))
            else:
                if action=='edit' and fact['claim_group_id']:
                    cur.execute('''SELECT count(*) AS n FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
                      AND contact_id=%s::uuid AND claim_group_id=%s::uuid''',(tenant,contact,str(fact['claim_group_id'])))
                    if cur.fetchone()['n']>1:raise TenantLinearError('Group edit requires review.',409)
                new_value=value if action=='edit' else fact['value']
                _,new_claim=_claim_token(tenant,new_value)
                new_quote_token=_source_token(tenant,'customer-memory-quote',evidence_quote)[1] if action=='edit' else fact['evidence_quote_token']
                status={'edit':'needs_review','confirm':'confirmed','outdated':'outdated','refute':'refuted'}[action]
                cur.execute('''UPDATE saas_customer_memory_facts SET value=%s,reliability=%s,claim_token=%s,evidence_quote_token=%s,version=version+1,
                  last_confirmed_at=CASE WHEN %s='confirmed' THEN now() WHEN %s='edit' THEN NULL ELSE last_confirmed_at END,
                  updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING version''',
                  (new_value,status,new_claim,new_quote_token,status,action,tenant,fact_id))
                version=cur.fetchone()['version']
                cur.execute('''INSERT INTO saas_customer_memory_fact_versions(fact_id,version,value,reliability,actor,claim_token,evidence_quote_token)
                  VALUES(%s::uuid,%s,%s,%s,%s,%s,%s)''',(fact_id,version,new_value,status,actor,new_claim,new_quote_token))
            _audit(cur,tenant,actor,'customer_memory_fact_'+action,'customer_memory_fact',fact_id)
            _bump_subject_revision(cur,tenant,contact,subject_revision,erasure=action=='delete')
            if owner_delete:
                cur.execute('''INSERT INTO saas_customer_memory_erasure_requests
                  (id,tenant_id,contact_id,fact_id,fact_version,requested_by,state)
                  VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,'pending')''',
                  (request_id,tenant,contact,fact_id,expected_version,actor))
        conn.commit()
    # Completion is intentionally unavailable until transport fencing exists.
    return {'request_id':request_id,'status':'pending'} if action=='delete' else {'id':fact_id,'version':version}


def erasure_status(profile,contact,request_id):
    """Owner-only recovery of non-content status, even with memory switched off."""
    contact=identifier(contact);request_id=identifier(request_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,_,_=_guard(cur,profile,'delete',erasure_recovery=True)
            cur.execute("SELECT to_regclass('saas_customer_memory_erasure_requests') AS relation")
            if not cur.fetchone()['relation']:raise TenantLinearError('Erasure unavailable.',404)
            cur.execute('''SELECT id,state,requested_at,completed_at FROM saas_customer_memory_erasure_requests
              WHERE id=%s::uuid AND tenant_id=%s::uuid AND contact_id=%s::uuid''',(request_id,tenant,contact))
            row=cur.fetchone()
            if not row:raise TenantLinearError('Erasure unavailable.',404)
            return {'request_id':str(row['id']),'status':row['state'],
                    'requested_at':row['requested_at'],'completed_at':row['completed_at']}


def erasure_statuses(profile,contact,before=None):
    """Current owner may page through non-content statuses for this contact."""
    contact=identifier(contact)
    if before is not None:before=identifier(before)
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,_,_=_guard(cur,profile,'delete',erasure_recovery=True)
            cur.execute("SELECT to_regclass('saas_customer_memory_erasure_requests') AS relation")
            if not cur.fetchone()['relation']:
                if before:raise TenantLinearError('Erasure unavailable.',404)
                return {'requests':[],'next_before':None}
            cutoff=None
            if before:
                cur.execute('''SELECT requested_at,id FROM saas_customer_memory_erasure_requests
                  WHERE tenant_id=%s::uuid AND contact_id=%s::uuid AND id=%s::uuid''',(tenant,contact,before))
                cutoff=cur.fetchone()
                if not cutoff:raise TenantLinearError('Erasure unavailable.',404)
            cur.execute('''SELECT id,state,requested_at,completed_at FROM saas_customer_memory_erasure_requests
              WHERE tenant_id=%s::uuid AND contact_id=%s::uuid
                AND (%s::timestamptz IS NULL OR (requested_at,id)<(%s::timestamptz,%s::uuid))
              ORDER BY requested_at DESC,id DESC LIMIT 26''',
              (tenant,contact,cutoff['requested_at'] if cutoff else None,
               cutoff['requested_at'] if cutoff else None,str(cutoff['id']) if cutoff else None))
            rows=cur.fetchall();items=[{'request_id':str(row['id']),'status':row['state'],
                     'requested_at':row['requested_at'],'completed_at':row['completed_at']}
                    for row in rows[:25]]
            return {'requests':items,'next_before':items[-1]['request_id'] if len(rows)>25 else None}
