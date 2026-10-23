"""Database invariants for the opt-in no-answer policy; uses isolated PGlite only."""
from test_tenant_support_inbox import database, db, seed_job, A, AGENT, CA
import tenant_ai_agent_no_answer as no_answer
import tenant_platform


def test_clarification_counter_is_idempotent_bounded_and_dialog_scoped(db, monkeypatch):
    monkeypatch.setattr(tenant_platform, '_connect', lambda: db)
    job=seed_job(db)
    assert no_answer.clarification_decision(job,'in-1',2)=={'state':'clarify','count':1}
    assert no_answer.clarification_decision(job,'in-1',2)=={'state':'duplicate','count':1}
    assert no_answer.clarification_decision(job,'in-2',2)=={'state':'clarify','count':2}
    assert no_answer.clarification_decision(job,'in-3',2)=={'state':'exhausted','count':2}
    other={**job,'conversation_id':'another-conversation'}
    assert no_answer.clarification_decision(other,'in-1',2)=={'state':'clarify','count':1}
    rows=db.execute('SELECT conversation_id,clarification_count FROM saas_ai_no_answer_state WHERE tenant_id=%s::uuid AND agent_id=%s::uuid ORDER BY conversation_id',(A,AGENT))
    assert rows==[{'conversation_id':'another-conversation','clarification_count':1},
                  {'conversation_id':'client','clarification_count':2}]


def test_stop_does_not_overwrite_an_existing_human_handoff(db, monkeypatch):
    monkeypatch.setattr(tenant_platform, '_connect', lambda: db)
    job=seed_job(db)
    db.execute("UPDATE saas_ai_conversation_state SET state='human',reason='operator_handoff' WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))
    db.commit()
    no_answer.stop_without_answer(job,{},'ai_no_answer:test')
    state=db.execute("SELECT state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    assert state=={'state':'human','reason':'operator_handoff'}
