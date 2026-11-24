"""Native PostgreSQL route transfer fencing; no provider calls."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
import psycopg
from psycopg.rows import dict_row

from test_connect_channel_multiprocess import database
from test_dialog_history_merge_projection import (merged,split,active,setup,staged_source,
    managed,gateway,pilot,db,_accept,
    test_reaction_capability_only_for_selected_actionable_history_route,
    test_selected_display_metadata_is_owner_only_without_changing_source_state)
from test_whatsapp_history_pairs import A,PROFILE,EMPLOYEE,PN,LID


def test_native_locked_companion_transfer_during_page_drops_old_authority(merged,monkeypatch):
    db,channel=merged
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED','1')
    _accept(db,channel,PN,33)
    _accept(db,channel,LID,33)
    db.commit()
    import tenant_operator_dialogs as dialogs
    from tenant_ai_dialog_scope import lock_dialog
    from tenant_linear_policy import TenantLinearError
    factory=lambda:psycopg.connect(db.dsn,row_factory=dict_row)
    monkeypatch.setattr(dialogs,'_connect',factory)
    first=dialogs.history(EMPLOYEE,'connect',41,channel,PN,combine_history=True)
    assert 'history_merge_id' in first['dialog']
    assert {m['source_lead_id'] for m in first['messages']}=={41,50}
    locked,release=Event(),Event()

    def transfer():
        with factory() as conn,conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout='750ms'")
            lock_dialog(cur,A,'connect',channel,LID)
            cur.execute("""UPDATE saas_dialog_workflows SET operator_id=11,revision=revision+1
              WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s""",(A,channel,LID))
            locked.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        worker=pool.submit(transfer)
        try:
            assert locked.wait(5)
            page=pool.submit(lambda: dialogs.history(EMPLOYEE,'connect',41,channel,PN,
                before=first['next_before'],combine_history=True)).result(3)
            assert 'history_merge_id' not in page['dialog']
            assert all(m['source_lead_id']==41 for m in page['messages'])
        finally:
            release.set()
        worker.result(5)
    later=dialogs.history(EMPLOYEE,'connect',41,channel,PN,combine_history=True)
    assert 'history_merge_id' not in later['dialog']
    assert {m['source_lead_id'] for m in later['messages']}=={41}
    with pytest.raises(TenantLinearError):
        dialogs.history(EMPLOYEE,'connect',50,channel,LID,combine_history=True)
    passive=dialogs.history(PROFILE,'connect',41,channel,PN,combine_history=True,owner_overview=True)
    assert passive['dialog']['owner_overview_read_only'] and not passive['dialog']['can_send']
    assert 'history_merge_action_routes' not in passive['dialog']
