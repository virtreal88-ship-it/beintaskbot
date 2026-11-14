#!/usr/bin/env python3
"""Narrow provenance preflight; read-only by default. --apply needs release approval."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

COLUMNS={'tenant_id':('uuid','NO'),'resource':('text','NO'),'external_id':('bigint','NO'),
 'account_id':('text','NO'),'connection_generation':('text','NO'),'raw_digest':('text','NO'),
 'source_revision':('text','NO'),'observed_at':('timestamp with time zone','NO')}


def validate(cur):
    cur.execute("SELECT to_regclass('saas_crm_board_provenance') IS NOT NULL AS present")
    if not (cur.fetchone() or {}).get('present'):return False
    cur.execute("""SELECT column_name,data_type,is_nullable FROM information_schema.columns
      WHERE table_schema=current_schema() AND table_name='saas_crm_board_provenance'""")
    actual={r['column_name']:(r['data_type'],r['is_nullable']) for r in cur.fetchall()}
    if actual!=COLUMNS:raise RuntimeError('board_provenance_schema_mismatch')
    cur.execute("""SELECT contype,pg_get_constraintdef(oid) AS definition FROM pg_constraint
      WHERE conrelid='saas_crm_board_provenance'::regclass""")
    constraints=cur.fetchall()
    if not any(r['contype']=='p' and r['definition']=='PRIMARY KEY (tenant_id, resource, external_id)' for r in constraints):
        raise RuntimeError('board_provenance_primary_key_mismatch')
    if not any(r['contype']=='c' and 'external_id > 0' in r['definition'] for r in constraints):
        raise RuntimeError('board_provenance_id_constraint_missing')
    if not any(r['contype']=='c' and 'resource' in r['definition'] and "'tasks'" in r['definition'] and "'leads'" in r['definition'] for r in constraints):
        raise RuntimeError('board_provenance_resource_constraint_missing')
    return True


def run(conn,*,apply=False):
    with conn.cursor() as cur:
        if not apply:cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("SET LOCAL lock_timeout='3s'")
        cur.execute("SET LOCAL statement_timeout='15s'")
        if apply:
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('tenant-platform-schema',0)) AS acquired")
            if (cur.fetchone() or {}).get('acquired') is not True:raise RuntimeError('platform_schema_busy')
        if validate(cur):return {'status':'compatible','changed':False}
        if not apply:return {'status':'missing','changed':False,'apply_required':True}
        from tenant_kommo_board_provenance import migrate
        migrate(cur) # This function creates exactly one new table and its PK.
        if not validate(cur):raise RuntimeError('board_provenance_creation_unconfirmed')
        return {'status':'compatible','changed':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='Create only missing provenance table; requires release approval.')
    args=parser.parse_args()
    from tenant_platform_core import _connect
    with _connect() as conn:
        result=run(conn,apply=args.apply)
    print(json.dumps(result))

if __name__=='__main__':main()
