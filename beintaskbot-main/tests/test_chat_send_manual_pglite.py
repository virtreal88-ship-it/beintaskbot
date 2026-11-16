"""Manual/OAuth send authority against disposable PostgreSQL/WASM; no sends."""
import json
from unittest.mock import Mock
import pytest
from cryptography.fernet import Fernet
from test_tenant_support_inbox import database, db, A, B
import tenant_chat_send_store as sender
from tenant_linear_policy import TenantLinearError


def test_manual_selection_identity_secret_and_revocation(db, monkeypatch):
    cipher=Fernet(Fernet.generate_key())
    monkeypatch.setattr(sender,'_connect',lambda:db)
    monkeypatch.setattr(sender,'_fernet',lambda:cipher)
    monkeypatch.setattr(sender,'TenantPolicy',lambda p:Mock())
    monkeypatch.setattr('tenant_dialog_access.require_source',lambda *a:None)
    monkeypatch.setattr('tenant_scoped_access_store.read_access_profile',lambda *a,**k:{'active':True,'tenant_status':'active','telegram_id':11})
    for tenant,key in ((A,'own'),(B,'foreign')):
        secret=cipher.encrypt(json.dumps({'tenant_id':tenant,'provider':'kommo_manual','access_token':key}).encode())
        db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain,secrets,connected_at) VALUES(%s::uuid,'kommo_manual','connected','client.kommo.com',%s,'2026-11-16')",(tenant,secret))
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain,connected_at) VALUES(%s::uuid,'kommo','disconnected','client.kommo.com','2026-11-16')",(A,))
    db.commit()
    session={'tenant_id':A,'telegram_id':11}
    with db.cursor() as cur:
        _,identity,row=sender.authority(cur,session,999)
    assert identity[0]=='manual:client.kommo.com'
    assert sender._kommo_send_tokens(row,A)['access_token']=='own'
    from tenant_crm_sync_store import connection_id
    with db.cursor() as cur:assert connection_id(cur,A,lock=True)==':'.join(identity)
    import tenant_deal_completion_provider as completion
    monkeypatch.setattr(completion,'_connect',lambda:db)
    monkeypatch.setattr(completion,'_ensure_schema',lambda c:None)
    assert completion.credentials(session,999,':'.join(identity))['access_token']=='own'
    with pytest.raises(TenantLinearError):completion.credentials(session,999,'client.kommo.com:other')

    with pytest.raises(TenantLinearError):sender._kommo_send_tokens(row,B)
    db.execute("UPDATE saas_tenant_integrations SET status='connected' WHERE tenant_id=%s::uuid AND provider='kommo'",(A,));db.commit()
    # Connected OAuth must not silently fall back even if its secret is invalid.
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):sender.authority(cur,session,999)
    db.execute("UPDATE saas_tenant_integrations SET status='disconnected' WHERE tenant_id=%s::uuid",(A,));db.commit()
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):sender.authority(cur,session,999)
