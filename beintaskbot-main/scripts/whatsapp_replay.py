"""Explicit replay shared by the CLI and the owner-authenticated HTTP boundary.

python scripts/whatsapp_replay.py --actor OWNER_ID --messages UUID UUID UUID UUID UUID --output /private/replay.json
Add --execute only after reviewing the saved preflight. Uses the worker's existing
credentials in situ; do not export production secrets to a laptop/test database.
"""
import argparse
import json
import os
import re
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tenant_ai_historical_replay import PILOT_TENANT, preflight, one
from tenant_platform import _connect


def write_private(path, value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2,default=str)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    except BaseException:
        if temporary.exists():temporary.unlink()
        raise


def run_batch(profile,messages,output,*,execute=False):
    if output.exists():raise RuntimeError('replay_output_already_exists')
    result={'preflight':preflight(profile,messages),'results':[],'recorded_paid_receipts':0}
    # Session lock is held on a dedicated connection for the whole batch.
    # Financial reservations still use the shared transaction-level tenant lock.
    with _connect() as lease:
        with lease.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked',
                        ('historical-replay:'+PILOT_TENANT,))
            if not cur.fetchone()['locked']:raise RuntimeError('Another replay is running.')
        lease.commit()
        try:
            write_private(output,result)
            if execute:
                for message in messages:
                    try:
                        item=one(profile,message)
                    except BaseException as exc:
                        code=str(exc) if re.fullmatch(r'[a-z0-9_]{1,100}',str(exc)) else 'replay_request_failed'
                        result['stopped']={'source_message_id':message,'error_type':type(exc).__name__,
                                           'reason':code,'paid_outcome_requires_ledger_check':True}
                        write_private(output,result)
                        raise
                    result['results'].append(item)
                    result['recorded_paid_receipts']=sum(len(x.get('receipts') or []) for x in result['results'])
                    write_private(output,result)
                # Comparison evidence is read only AFTER all AI responses and
                # is never appended to the model's time-bounded input.
                with _connect() as conn:
                    with conn.cursor() as cur:
                        for item in result['results']:
                            cur.execute('''SELECT m.body,m.direction,m.message_type,m.id,m.author_name,m.raw,m.channel
                              FROM saas_crm_messages m JOIN saas_crm_messages source
                                ON source.tenant_id=m.tenant_id AND source.kommo_lead_id=m.kommo_lead_id
                                  AND source.channel=m.channel
                              WHERE source.tenant_id=%s::uuid AND source.id=%s::uuid
                                AND (m.direction='outgoing' OR m.raw ? 'verified_employee_id')
                                AND (COALESCE(m.happened_at,m.created_at),m.created_at)>
                                  (COALESCE(source.happened_at,source.created_at),source.created_at)
                              ORDER BY COALESCE(m.happened_at,m.created_at),m.created_at,m.id LIMIT 30''',
                              (PILOT_TENANT,item['source_message_id']))
                            from tenant_operator_dialogs import _message
                            comparison=[_message(dict(row)) for row in cur.fetchall()]
                            item['comparison_only_employee_replies']=[row for row in comparison if row['author_type'] in {'operator','employee'}][:8]
                result['final_budget']=preflight(profile,messages)
                write_private(output,result)
        finally:
            with lease.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',
                            ('historical-replay:'+PILOT_TENANT,))
            lease.commit()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actor',type=int,required=True)
    parser.add_argument('--messages',nargs=5,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    result=run_batch({'tenant_id':PILOT_TENANT,'telegram_id':args.actor},args.messages,args.output,execute=args.execute)
    print(json.dumps({'output':str(args.output),'completed':len(result['results']),'executed':args.execute}))


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Never print libpq, provider responses, keys or client text.
        code=str(error) if re.fullmatch(r'[a-z0-9_]{1,100}',str(error)) else 'replay_request_failed'
        print(json.dumps({'stopped':True,'error_type':type(error).__name__,'reason':code}),file=sys.stderr)
        sys.exit(2)
