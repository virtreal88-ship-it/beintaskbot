import pytest

from test_tenant_support_inbox import A, B, PROFILE, database, db


def test_delete_completed_order_is_owner_only_and_preserves_financial_history(db,monkeypatch):
    import tenant_hot_orders as orders
    import tenant_hot_order_schema as schema
    monkeypatch.setattr(orders,'_connect',lambda:db)
    monkeypatch.setattr(schema,'_ready',False)
    schema.ensure_hot_order_schema(db)
    profiles = [
        {**PROFILE, 'role':'owner'},
        {'tenant_id':A,'telegram_id':33,'role':'owner'},  # stale role in the caller
        {'tenant_id':B,'telegram_id':22,'role':'owner'},
    ]
    for profile in profiles:
        profile.update(active=True,tenant_status='active',modules={'hot_orders':True})
    db.execute("""UPDATE saas_tenants SET modules='{"hot_orders":true}'::jsonb WHERE id IN (%s,%s)""",(A,B))
    db.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,permissions)
        VALUES(%s,33,'admin','["hot_orders"]')""",(A,))
    id='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
    db.execute("""INSERT INTO saas_hot_orders(tenant_id,id,request_id,fingerprint,status,service_id,client_name,
        description,reward_minor,created_by,completed_at) VALUES(%s,%s,%s,'fixture','completed','repair',
        'Test customer','Completed repair',2500,11,now())""",(A,id,id))
    db.commit()

    assert orders.list_orders(profiles[0])['orders'][0]['actions']['delete'] is True
    for profile, status in zip(profiles[1:], (403,404)):
        with pytest.raises(orders.TenantHotOrderError) as error:
            orders.change_order(profile,order_id=id,action='delete')
        assert error.value.status == status
    for _ in range(2):
        assert orders.change_order(profiles[0],order_id=id,action='delete') == {'id':id,'deleted':True}
    assert orders.list_orders(profiles[0])['total'] == 0
    assert db.execute('SELECT status,archived_at IS NOT NULL AS archived,reward_minor FROM saas_hot_orders WHERE id=%s',(id,)) == [
        {'status':'completed','archived':True,'reward_minor':2500}]
    with pytest.raises(orders.TenantHotOrderError) as error:
        orders.change_order(profiles[0],order_id=id,action='cancel')
    assert error.value.status == 404
    assert len(db.query("SELECT id FROM saas_tenant_audit_events WHERE action='hot_order_delete' AND entity_id=%s",(id,))) == 1
