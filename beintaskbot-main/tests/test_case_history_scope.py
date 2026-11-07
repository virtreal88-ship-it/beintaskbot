"""Case history binds aliases using the existing authorized merge reader.

Synthetic PGlite only: no provider, AI, client send or production database.
"""
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, PROFILE
from test_connect_dependent_reconcile import seed, request, JID, LID, LEADS
from test_connect_dialog_equivalence import prepare, candidate, command, apply, native_dependencies
import tenant_connect_direct_reconcile as reconcile
import tenant_operator_dialogs as dialogs
import tenant_support_inbox_store as support
from tenant_linear_policy import TenantLinearError


def route(lead=8102, conversation=LID):
    return {'provider':'connect','lead_id':lead,'channel_id':CA,'conversation_id':conversation}


@pytest.mark.parametrize('kind', ['retained','physical'])
def test_case_history_scope_proves_original_and_canonical_with_pagination(db, monkeypatch, kind):
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    if kind == 'retained':
        prepare(db,monkeypatch)
        native_dependencies(db)
        apply(db,command(candidate(db)))
    else:
        seed(db,monkeypatch)
        reconcile.apply(PROFILE,request())
    first=dialogs.history(PROFILE,'connect',8102,CA,LID)
    second=dialogs.history(PROFILE,'connect',8102,CA,LID,before=first['next_before'])
    expected={'tenant_id':A,'requested_route':route(),'canonical_route':route(8101,JID)}
    for page in (first,second):
        assert page['history_scope'] == expected
        assert {key:page['dialog'][key] for key in route()} == expected['canonical_route']
    messages=first['messages']+second['messages']
    assert len(messages) == len({m['id'] for m in messages}) == 85
    assert first['next_before'] and second['next_before'] is None
    if kind == 'retained':
        case=db.execute('SELECT id FROM saas_support_tickets WHERE tenant_id=%s::uuid AND conversation_id=%s',(A,LID))[0]['id']
        db.commit()
        detail=support.detail(PROFILE,str(case))
        assert detail['ticket']['conversation_id'] == LID
        assert {m['id'] for m in detail['messages']} == {m['id'] for m in messages if m['source_lead_id']==8102}
        # A withdrawn equivalence no longer attests that the alias is canonical.
        db.execute('UPDATE saas_connect_dialog_links SET active=FALSE WHERE tenant_id=%s::uuid AND channel_id=%s::uuid',(A,CA))
        db.commit()
        assert dialogs.history(PROFILE,'connect',8102,CA,LID)['history_scope'] == {
            'tenant_id':A,'requested_route':route(),'canonical_route':route()}


@pytest.mark.parametrize('kind', ['retained','physical'])
def test_case_history_scope_never_proves_mismatched_authority(db, monkeypatch, kind):
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    if kind == 'retained':
        prepare(db,monkeypatch)
        apply(db,command(candidate(db)))
    else:
        seed(db,monkeypatch)
        reconcile.apply(PROFILE,request())
    for profile,provider,lead,channel,conversation in (
        ({'tenant_id':B,'telegram_id':22},'connect',8102,CA,LID),
        ({**PROFILE,'telegram_id':22},'connect',8102,CA,LID),
        (PROFILE,'connect',8102,CB,LID),
        (PROFILE,'connect',8101,CA,LID),
        (PROFILE,'connect',8102,CA,JID),
        (PROFILE,'connect',8102,CA,'unrelated@lid'),
        (PROFILE,'unknown',8102,CA,LID),
    ):
        with pytest.raises(TenantLinearError):
            dialogs.history(profile,provider,lead,channel,conversation)
