"""Read-only inventory of direct-chat identity evidence. No schema initialization.

Run inside the authorized worker environment; output is private customer data.
Neither a display name nor a numeric LID is accepted as a phone pairing.
"""
import argparse
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tenant_platform import _connect
from tenant_ai_historical_replay import PILOT_TENANT, PILOT_CHANNEL


def pairs(messages):
    links={};receipts={}
    for message in messages:
        raw=message.get('raw') or {}
        if not isinstance(raw,dict):continue
        pair=raw.get('directIdentity') or {}
        if not isinstance(pair,dict):continue
        lid,jid=pair.get('lid',''),pair.get('jid','')
        if not (re.fullmatch(r'[0-9]{5,22}@lid',str(lid)) and
                re.fullmatch(r'[0-9]{5,22}@s\.whatsapp\.net',str(jid)) and
                raw.get('conversationId') in {lid,jid}):continue
        # These are authenticated Connect copies, not browser-supplied claims.
        if not str(message.get('external_id') or '').startswith('connect:'):continue
        links.setdefault(lid,set()).add(jid);links.setdefault(jid,set()).add(lid)
        receipts.setdefault((lid,jid),[]).append(str(message['id']))
    return [{'lid':lid,'jid':jid,'evidence_message_ids':ids,
        'identity_unambiguous':links[lid]=={jid} and links[jid]=={lid}}
        for (lid,jid),ids in sorted(receipts.items())]


def inventory(conn,profile):
    with conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("SET LOCAL statement_timeout='20s'")
        tenant=str(profile.get('tenant_id') or '')
        if tenant!=PILOT_TENANT:raise ValueError('Only the owner-approved test tenant is in scope.')
        cur.execute('''SELECT m.role,m.active,t.status FROM saas_tenant_members m
          JOIN saas_tenants t ON t.id=m.tenant_id WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s''',
          (tenant,profile.get('telegram_id')))
        member=cur.fetchone() or {}
        if member.get('role')!='owner' or member.get('active') is not True or member.get('status')!='active':
            raise PermissionError('Active company owner required.')
        cur.execute('''SELECT connect_channel_id,name,status,active FROM saas_connect_channels
          WHERE tenant_id=%s::uuid ORDER BY connect_channel_id''',(tenant,))
        channels=[dict(row) for row in cur.fetchall()]
        cur.execute('''SELECT d.conversation_id,d.lead_id,d.contact_name,d.phone,
          count(m.id) AS messages,count(m.id) FILTER(WHERE m.direction='incoming') AS incoming,
          count(m.id) FILTER(WHERE m.direction='outgoing') AS outgoing
          FROM saas_connect_conversations d LEFT JOIN saas_crm_messages m ON m.tenant_id=d.tenant_id
            AND m.channel='connect:'||d.connect_channel_id::text AND m.kommo_lead_id=d.lead_id
          WHERE d.tenant_id=%s::uuid AND d.connect_channel_id=%s::uuid
            AND d.conversation_id ~ '^[0-9]{5,22}@(lid|s[.]whatsapp[.]net)$'
          GROUP BY d.conversation_id,d.lead_id,d.contact_name,d.phone ORDER BY d.lead_id''',(tenant,PILOT_CHANNEL))
        routes=[dict(row) for row in cur.fetchall()]
        cur.execute('''SELECT id,external_id,raw FROM saas_crm_messages WHERE tenant_id=%s::uuid
          AND channel=%s AND raw ? 'directIdentity' ''',(tenant,'connect:'+PILOT_CHANNEL))
        identities=pairs([dict(row) for row in cur.fetchall()])
        existing={row['conversation_id'] for row in routes}
        proven=[pair for pair in identities if pair['identity_unambiguous'] and pair['lid'] in existing and pair['jid'] in existing]
        cur.execute('''SELECT m.id,d.conversation_id,m.body,m.message_type,COALESCE(m.happened_at,m.created_at) AS at
          FROM saas_crm_messages m JOIN saas_connect_conversations d ON d.tenant_id=m.tenant_id
            AND d.lead_id=m.kommo_lead_id AND m.channel='connect:'||d.connect_channel_id::text
          WHERE m.tenant_id=%s::uuid AND d.connect_channel_id=%s::uuid
            AND d.conversation_id ~ '^[0-9]{5,22}@s[.]whatsapp[.]net$' AND m.direction='incoming'
            AND NULLIF(btrim(m.body),'') IS NOT NULL AND NOT (m.raw ? 'verified_employee_id')
            AND COALESCE(m.happened_at,m.created_at)>=now()-interval '7 days'
          ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT 200''',(tenant,PILOT_CHANNEL))
        candidates=[dict(row) for row in cur.fetchall()]
    return {'channel_id':PILOT_CHANNEL,'channels':channels,'direct_routes':routes,
        'provider_proven_pairs':identities,'proven_split_count':len(proven),
        'unpaired_lid_count':sum(row['conversation_id'].endswith('@lid') and
            not any(p['identity_unambiguous'] and p['lid']==row['conversation_id'] for p in identities) for row in routes),
        'replay_candidates':candidates,'writes_performed':False,
        'note':'Merge requires a fresh locked backup of both stores and all references. This inventory never authorizes a merge by name.'}


if __name__=='__main__':
    from whatsapp_replay import write_private
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actor',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Use a new private output file.')
    try:
        with _connect() as conn:result=inventory(conn,{'tenant_id':PILOT_TENANT,'telegram_id':args.actor})
        write_private(args.output,result)
        print(json.dumps({'output':str(args.output),'proven_split_count':result['proven_split_count'],
            'unpaired_lid_count':result['unpaired_lid_count'],'writes_performed':False}))
    except Exception as exc:
        print(json.dumps({'blocked':True,'error_type':type(exc).__name__}),file=sys.stderr);sys.exit(2)
