"""Published instruction reads and revocation fences on isolated PostgreSQL."""
import json
import uuid

import psycopg
from psycopg.rows import dict_row
import pytest

from test_tenant_support_inbox import A, B, db
from test_connect_channel_multiprocess import database
from tenant_linear_policy import TenantLinearError
import tenant_instruction_library as instructions


@pytest.fixture
def catalog(db, monkeypatch):
    factory = lambda: psycopg.connect(db.dsn, row_factory=dict_row)
    monkeypatch.setattr(instructions, '_connect', factory)
    instructions.schema(db)
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,permissions) VALUES(%s,33,'worker','[]')", (A,))
    keys = {}
    for tenant, state in [(A, 'published'), (A, 'draft'), (B, 'published')]:
        key, asset = str(uuid.uuid4()), str(uuid.uuid4())
        keys[(tenant, state)] = (key, asset)
        document = {'title': tenant+' '+state, 'operation': 'synthetic.read', 'language': 'ru',
                    'slides': [{'image': {'asset_id': asset+'.jpg'}}]}
        db.execute('INSERT INTO saas_instruction_assets(tenant_id,id,body,width,height,sha256) VALUES(%s,%s,%s,1,1,%s)', (tenant, asset, b'synthetic screenshot', asset))
        db.execute('INSERT INTO saas_instruction_lessons(tenant_id,id,state,doc,published_doc) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb)', (tenant, key, state, json.dumps({'title': 'PRIVATE DRAFT'}), json.dumps(document)))
    db.commit()
    return factory, keys, {'tenant_id': A, 'telegram_id': 33}


def test_published_instructions_shared_without_ai_rights_and_no_drafts(catalog):
    factory, keys, profile = catalog
    own, asset = keys[(A, 'published')]
    assert [r['id'] for r in instructions.published_list(profile)['items']] == [own]
    assert instructions.published_read(profile, own)['doc']['title'] == A+' published'
    assert instructions.published_asset(profile, own, asset+'.jpg') == b'synthetic screenshot'
    for target in [(A, 'draft'), (B, 'published')]:
        key, foreign_asset = keys[target]
        for read in [lambda: instructions.published_read(profile, key),
                     lambda: instructions.published_asset(profile, key, foreign_asset+'.jpg')]:
            with pytest.raises(TenantLinearError) as denied:
                read()
            assert denied.value.status == 404
    with pytest.raises(TenantLinearError) as denied:
        instructions.operation(profile, 'list')
    assert denied.value.status == 403


@pytest.mark.parametrize('target', ['member', 'company'])
def test_published_catalog_holds_revocation_fence_until_read_finishes(catalog, target):
    factory, keys, profile = catalog
    with factory() as reader, factory() as writer:
        with reader.cursor() as cur:
            assert instructions._authorize_published_catalog(cur, profile) == (A, 33)
        writer.execute("SET LOCAL lock_timeout='100ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            if target == 'member':
                writer.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=33', (A,))
            else:
                writer.execute("UPDATE saas_tenants SET status='suspended' WHERE id=%s", (A,))
        writer.rollback()
        reader.commit()
        if target == 'member':
            writer.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=33', (A,))
        else:
            writer.execute("UPDATE saas_tenants SET status='suspended' WHERE id=%s", (A,))
        writer.commit()
    own, asset = keys[(A, 'published')]
    for read in [lambda: instructions.published_list(profile),
                 lambda: instructions.published_read(profile, own),
                 lambda: instructions.published_asset(profile, own, asset+'.jpg')]:
        with pytest.raises(TenantLinearError) as denied:
            read()
        assert denied.value.status == 403


def test_default_deny_blocks_list_detail_assets_until_explicit_grant(catalog, monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    factory, keys, profile = catalog
    own, asset = keys[(A, 'published')]
    with factory() as conn:
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'employee_access_defaults','{\"deny_unset\":true}')", (A,))
    reads = [lambda: instructions.published_list(profile),
             lambda: instructions.published_read(profile, own),
             lambda: instructions.published_asset(profile, own, asset+'.jpg')]
    for read in reads:
        with pytest.raises(TenantLinearError) as denied: read()
        assert denied.value.status == 403
    with factory() as conn:
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb)", (A,json.dumps({'33':{'access_scopes':{'instructions':{'view':'all'}}}})))
    for read in reads: assert read()
    foreign, foreign_asset = keys[(B,'published')]
    with pytest.raises(TenantLinearError) as denied: instructions.published_asset(profile,foreign,foreign_asset+'.jpg')
    assert denied.value.status == 404
