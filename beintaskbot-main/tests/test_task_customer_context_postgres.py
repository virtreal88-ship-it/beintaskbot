"""Isolated PostgreSQL + real cookie endpoint proves customer read boundaries."""
import asyncio
from aiohttp.test_utils import TestClient, TestServer

from test_task_read_save_flow_postgres import (
    A, B, CONTACT, task_flow, staff, db, database, _app, _get, _state,
)


def test_customer_details_are_scoped_without_provider_reads(task_flow):
    f = task_flow
    with f['factory']() as conn:
        conn.execute("UPDATE saas_crm_native_contacts SET phone='+994000000001' WHERE tenant_id=%s::uuid AND id=%s::uuid", (A, CONTACT))
        conn.execute("UPDATE saas_crm_deals SET contact_name='Allowed Kommo client',phone='+994000000002' WHERE tenant_id=%s::uuid AND kommo_lead_id=101", (A,))
        conn.execute("UPDATE saas_crm_deals SET contact_name='FOREIGN PRIVATE',phone='+994000000003' WHERE tenant_id=%s::uuid AND kommo_lead_id=101", (B,))
        # The operator can read their marker-owned task but has no access to
        # this deal. A join solely on task visibility would disclose its client.
        conn.execute('UPDATE saas_crm_tasks SET kommo_lead_id=101 WHERE tenant_id=%s::uuid AND kommo_task_id=502', (A,))
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            owner = f['session'].issue(A, 11)
            status, native = await _get(http, owner)
            assert status == 200
            assert any(row['contact_name'] == 'Synthetic task client' and row['phone'] == '+994000000001' for row in native['tasks'])
            status, external = await _get(http, owner, source='kommo')
            assert status == 200
            task = next(row for row in external['tasks'] if row['id'] == '501')
            assert task['contact_name'] == 'Allowed Kommo client' and task['phone'] == '+994000000002'
            assert 'FOREIGN PRIVATE' not in str(external)
            operator = f['session'].issue(A, 83)
            status, native = await _get(http, operator, actor=83)
            assert status == 200 and native['tasks']
            assert all(row['contact_name'] == row['phone'] == '' for row in native['tasks'])
            status, external = await _get(http, operator, source='kommo', actor=83)
            assert status == 200
            task = next(row for row in external['tasks'] if row['id'] == '502')
            assert task['contact_name'] == task['phone'] == '' and not task.get('deal_title')
    asyncio.run(scenario())
    assert _state(f) == before and not f['provider'].calls
