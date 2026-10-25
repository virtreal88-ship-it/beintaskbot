"""Owner-scoped physical reconciliation facade; all writes reuse Issue 248."""
import hashlib
import hmac
import json
import uuid

import tenant_connect_direct_reconcile as reconcile
from tenant_ai_dialogue_merge import _owner, _token
from tenant_ai_agents_store import _audit
from tenant_linear_policy import TenantLinearError

WRITE_SCOPE = 'ai_learning.direct_reconcile'
PREFIX = 'whatsapp_direct_reconcile_'
SCHEMA_VERSION = '2026-10-25.1'


def _validate(args, allowed):
    if not isinstance(args, dict) or set(args)-set(allowed):
        raise TenantLinearError('Invalid physical reconciliation arguments.',400)


def _summary(snapshot):
    messages=snapshot.get('messages') or []
    return {'messages':len(messages), 'media':sum(bool(m.get('media_url')) for m in messages),
            'routes':len(snapshot.get('routes') or []),
            'states':sorted({s['state'] for s in snapshot.get('states') or []}),
            'jobs':len(snapshot.get('jobs') or []), 'drafts':len(snapshot.get('drafts') or []),
            'read_markers':len(snapshot.get('reads') or [])}


def _channel(cur, tenant, channel):
    cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND active=TRUE',(tenant,channel))
    if not cur.fetchone(): raise TenantLinearError('Reconciliation channel unavailable.',403)


def preview(cur, cfg, args):
    _validate(args, {'channel_id','cursor','limit','pair_id'})
    tenant,actor=_owner(cur,cfg)
    limit=args.get('limit',25)
    if type(limit) is not int or not 1<=limit<=25: raise TenantLinearError('Page limit must be 1–25.',400)
    channel=str(args.get('channel_id') or cfg['AI_LEARNING_MCP_CHANNEL_ID'])
    if channel!='all':
        try: channel=str(uuid.UUID(channel))
        except ValueError: raise TenantLinearError('Invalid channel.',400) from None
        _channel(cur,tenant,channel)
    # Company-wide scan is read-only. Mutation remains bound to the OAuth
    # connection's exact channel; separate company numbers are never paired.
    cur.execute('SELECT connect_channel_id::text AS channel FROM saas_connect_channels WHERE tenant_id=%s::uuid AND active=TRUE AND (%s=\'all\' OR connect_channel_id::text=%s) ORDER BY connect_channel_id',(tenant,channel,channel))
    channels=[r['channel'] for r in cur.fetchall()]
    entries=[]
    from tenant_connect_identity import inventory
    for ch in channels:
        scan=inventory(cur,tenant,ch)
        items=reconcile._candidates(cur,tenant,ch)
        cur.execute('SELECT pair_id,state,snapshot FROM saas_connect_direct_merge_backups WHERE tenant_id=%s::uuid AND channel_id=%s::uuid',(tenant,ch))
        backups={r['pair_id']:r for r in cur.fetchall()}
        for item in items:
            backup=backups.get(item['pair_id'])
            proof=next(p for p in scan['pairs'] if reconcile._id(tenant,ch,p['canonical_id'],p['split_id'])==item['pair_id'])
            references={key: _token(cfg,'direct-route',tenant,ch,proof[value]) for key,value in [('canonical_ref','canonical_id'),('split_ref','split_id')]}
            state='already_merged' if backup and backup['state']=='merged' else ('verified_eligible' if item['eligible'] else 'review_blocked')
            view={**item,**references,'kind':'pair','state':state,'physical':True,
                  'eligible':state=='verified_eligible','oauth_channel_writable':ch==cfg['AI_LEARNING_MCP_CHANNEL_ID']}
            private_ids=[proof['canonical_id'],proof['split_id']]
            leads=[int(scan['routes'][identity]['lead_id']) for identity in private_ids if identity in scan['routes']]
            cur.execute("""SELECT id::text AS message_ref,message_type,
                (NULLIF(media_url,'') IS NOT NULL) AS has_media,
                (raw ? 'directIdentity') AS has_provider_pair
                FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s
                  AND kommo_lead_id=ANY(%s::bigint[]) ORDER BY id LIMIT 25""",(tenant,'connect:'+ch,leads))
            view['evidence_refs']=[dict(r) for r in cur.fetchall()]
            cur.execute("SELECT count(*) AS total,count(*) FILTER(WHERE NULLIF(media_url,'') IS NOT NULL) AS media FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=ANY(%s::bigint[])",(tenant,'connect:'+ch,leads))
            view['message_media_counts']=dict(cur.fetchone())
            view['evidence_policy']='References correlate stored provider evidence only; names, owner guesses and direction splits never establish identity.'
            if state=='already_merged':
                view['conflicts']=[]
                s=backup['snapshot']
                current=reconcile.dependencies.capture(cur,tenant,ch,s['ids'],s['leads'])
                view['verification']=_summary(current)
                from tenant_operator_access import assert_route
                try:
                    redirected=assert_route(cur,tenant,None,'connect',s['ids'][1],ch,s['leads'][1],allow_merged=True)
                    view['old_link_redirect_preserved']=int(redirected['lead_id'])==int(s['leads'][0])
                except TenantLinearError:
                    view['old_link_redirect_preserved']=False
            entries.append(view)
        for identity,route in scan['routes'].items():
            ref=_token(cfg,'direct-route',tenant,ch,identity)
            entries.append({'kind':'route','route_ref':ref,'channel_id':ch,
                'messages':int(route['messages']),'incoming':int(route['incoming']),'outgoing':int(route['outgoing']),
                'identity_kind':'lid' if identity.endswith('@lid') else 'phone_jid'})
    entries.sort(key=lambda r:(r['channel_id'],r['kind'],r.get('pair_id',r.get('route_ref',''))))
    if args.get('pair_id'): entries=[r for r in entries if r.get('pair_id')==args['pair_id']]
    fingerprint=hashlib.sha256(json.dumps(entries,sort_keys=True).encode()).hexdigest()
    offset=0
    if args.get('cursor'):
        parts=str(args['cursor']).split('.')
        if len(parts)!=3 or not parts[0].isdigit() or parts[1]!=fingerprint or not hmac.compare_digest(parts[2],_token(cfg,'direct-page',tenant,channel,args.get('pair_id',''),parts[0],fingerprint)):
            raise TenantLinearError('Scan changed or cursor invalid; restart the scan.',409)
        offset=int(parts[0])
    page=entries[offset:offset+limit];end=offset+len(page)
    cursor=f'{end}.{fingerprint}.'+_token(cfg,'direct-page',tenant,channel,args.get('pair_id',''),str(end),fingerprint) if end<len(entries) else None
    _audit(cur,tenant,actor,'mcp_direct_reconcile_preview','mcp_tool',PREFIX+'preview',{'count':len(page),'channels':len(channels)})
    return {'schema_version':SCHEMA_VERSION,'physical':True,'items':page,'total':len(entries),'next_cursor':cursor,'scan_complete':cursor is None,
            'counts':{state:sum(r.get('state')==state for r in entries) for state in ('verified_eligible','review_blocked','already_merged')},
            'identity_policy':'Only signed provider pairs or protected persisted aliases authorize identity; hints never do.'}


def execute(cur,cfg,name,args):
    tenant,actor=_owner(cur,cfg)
    channel=str(cfg['AI_LEARNING_MCP_CHANNEL_ID']);_channel(cur,tenant,channel)
    profile={'tenant_id':tenant,'telegram_id':actor}
    operation=name.removeprefix(PREFIX)
    if operation=='preview': return preview(cur,cfg,args)
    if operation=='rollback_preflight':
        _validate(args,{'pair_id'})
        if set(args)!={'pair_id'}:raise TenantLinearError('Pair required.',400)
        result=reconcile.rollback_preflight(profile,args['pair_id'],connection=cur.connection,channel_id=channel)
        # Timestamp tolerances are useful internally, but contain private
        # route identifiers. MCP exposes only the count of preserved touches.
        result['preserved_timestamp_touches']=len(result.get('preserved_timestamp_touches') or [])
        return result
    _validate(args,{'pair_id','precondition','request_id','confirm'})
    if set(args)!={'pair_id','precondition','request_id','confirm'} or args['confirm'] is not True:
        raise TenantLinearError('Exact owner confirmation required.',400)
    if operation not in {'apply','rollback'}:raise TenantLinearError('Unknown physical operation.',400)
    result=reconcile.apply(profile,{**args,'operation':'merge' if operation=='apply' else 'rollback'},connection=cur.connection,channel_id=channel)
    return {key:(len(value or []) if key=='preserved_timestamp_touches' else value)
            for key,value in result.items() if key!='request_digest'}


def definitions():
    pair={'type':'string','pattern':'^[a-f0-9]{64}$'}
    write={'type':'object','properties':{'pair_id':pair,'precondition':pair,'request_id':{'type':'string','format':'uuid'},'confirm':{'type':'boolean','const':True}},'required':['pair_id','precondition','request_id','confirm'],'additionalProperties':False}
    result=[]
    for operation,description,schema,readonly in [
        ('preview','Owner-only bounded physical duplicate scan and evidence review. channel_id=all scans company channels separately. Follow next_cursor for the complete inventory; restart if stale. Opaque pair/route refs, provider provenance, dependency blockers and already-merged verification only; no private identifiers or messages.',{'type':'object','properties':{'channel_id':{'type':'string'},'pair_id':pair,'cursor':{'type':'string'},'limit':{'type':'integer','minimum':1,'maximum':25}},'additionalProperties':False},True),
        ('apply','PHYSICAL CRM message/route reconciliation, not logical linking. Only an owner-authorized verified same-channel stopped pair with a fresh preview precondition, request UUID and confirm=true. Reuses guarded backup, migration, audit and rollback. Never sends messages or resumes AI.',write,False),
        ('rollback_preflight','Owner-only dry-run verification of rollback for one physically merged pair in the OAuth channel. Does not restore or change customer data.',{'type':'object','properties':{'pair_id':pair},'required':['pair_id'],'additionalProperties':False},True),
        ('rollback','PHYSICAL rollback of an unchanged backup under the same guards. Requires explicit owner confirmation and fresh preview. Do not use as a production smoke test.',write,False)]:
        result.append({'name':PREFIX+operation,'description':description,'inputSchema':schema,'annotations':{'readOnlyHint':readonly,'destructiveHint':not readonly,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':['ai_learning.read' if readonly else WRITE_SCOPE]}]})
    return result
