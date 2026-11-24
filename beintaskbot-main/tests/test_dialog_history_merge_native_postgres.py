"""Native shared vs independent worker transactions against a localhost fixture."""
import asyncio
import pytest
import psycopg
from psycopg.rows import dict_row

import tenant_dialog_history_merge_jobs as jobs
from test_dialog_history_merge_jobs import (merge_case, active, setup, staged_source, managed,
                                           gateway, pilot, db, command,
                                           test_independent_display_choices_snapshot_never_affects_provider_route_or_proof,
                                           test_stale_or_unoffered_display_choices_leave_no_job,
                                           test_display_edit_between_proof_and_publication_requires_fresh_comparison,
                                           test_capacity_limits_new_work_without_invalidating_existing_pairs)
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A, PROFILE
from test_whatsapp_history_pairs import PN, LID


@pytest.mark.parametrize('race',['cancel','accept','inbound','scope_rollback'])
def test_workers_coalesce_without_holding_route_locks_during_provider_read(merge_case,monkeypatch,race):
    db,channel=merge_case
    factory=lambda:psycopg.connect(db.dsn,row_factory=dict_row)
    monkeypatch.setattr(jobs,'_connect',factory)
    job=jobs.action(PROFILE,command(channel))
    calls=[]
    async def run():
        provider_entered=asyncio.Event()
        provider_released=asyncio.Event()
        async def device(route):
            calls.append(route['conversation_id'])
            provider_entered.set()
            await provider_released.wait()
            return {'phone':'+'+PN.split('@')[0]}
        first=asyncio.create_task(jobs.tick(device))
        await asyncio.wait_for(provider_entered.wait(),timeout=5)
        second=asyncio.create_task(jobs.tick(device))
        await asyncio.wait_for(second,timeout=3)
        if race=='cancel':
            result=await asyncio.to_thread(jobs.action,PROFILE,{'operation':'cancel','job_id':job['id']})
            assert result['state']=='cancelled'
        elif race=='accept':
            # Real concurrent transaction takes the original dialog route lock
            # while the worker is outside PostgreSQL waiting for proof.
            def accept():
                from tenant_ai_dialog_scope import lock_dialog
                with factory() as conn,conn.cursor() as cur:
                    cur.execute("SET LOCAL lock_timeout='750ms'")
                    lock_dialog(cur,A,'connect',channel,PN)
                    cur.execute("""INSERT INTO saas_dialog_workflows
                        (tenant_id,channel_id,conversation_id,id,state,operator_id)
                        VALUES (%s,%s,%s,gen_random_uuid(),'accepted',11)""",(A,channel,PN))
            await asyncio.to_thread(accept)
        elif race=='scope_rollback':
            monkeypatch.delenv('DIALOG_HISTORY_MERGE_SCOPE')
        else:
            def arrival():
                from tenant_ai_dialog_scope import lock_dialog
                with factory() as conn,conn.cursor() as cur:
                    cur.execute("SET LOCAL lock_timeout='750ms'")
                    lock_dialog(cur,A,'connect',channel,LID)
                    cur.execute("""INSERT INTO saas_crm_messages
                      (id,tenant_id,kommo_lead_id,external_id,channel,direction,body)
                      VALUES(gen_random_uuid(),%s,50,'native-before-publish',%s,'incoming','fixture')""",
                      (A,'connect:'+channel))
            await asyncio.to_thread(arrival)
        provider_released.set()
        await asyncio.wait_for(first,timeout=5)
        await jobs.tick(device)
    asyncio.run(run())
    assert len(calls)==1
    with factory() as conn:
        state=conn.execute("SELECT state,code FROM saas_dialog_history_merge_jobs WHERE id=%s",(job['id'],)).fetchone()
        assert state=={
            'cancel':{'state':'cancelled','code':''},
            'accept':{'state':'blocked','code':'lifecycle_in_use'},
            'inbound':{'state':'completed','code':''},
            'scope_rollback':{'state':'running','code':''},
        }[race]
        if race=='inbound':
            assert conn.execute("""SELECT kommo_lead_id FROM saas_crm_messages
                WHERE tenant_id=%s AND external_id='native-before-publish'""",(A,)).fetchone()=={'kommo_lead_id':50}
        assert not conn.execute('SELECT 1 FROM saas_connect_dialog_links WHERE tenant_id=%s',(A,)).fetchone()
