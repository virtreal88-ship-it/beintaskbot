"""Retrieval only against exact published texts; no generation or production IO."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from test_tenant_ai_testing import database,db,sandbox,new,message,uid
from test_tenant_support_inbox import A,B,AGENT,PROFILE
import tenant_ai_agents_store as agents
import tenant_ai_testing_store as store
import tenant_ai_testing_service as service
from tenant_ai_agents_policy import chunk_text

PUBLISHED=json.loads((Path(__file__).parent/'fixtures/inventory_published_knowledge.json').read_text())

@pytest.fixture
def indexed(sandbox,monkeypatch):
    provider=Mock(side_effect=AssertionError('Retrieval must not call a provider'))
    monkeypatch.setattr(service,'generate',provider)
    sources=[]
    for item in PUBLISHED:
        assert hashlib.sha256(item['content'].encode()).hexdigest()==item['content_hash']
        source=agents.save_knowledge(PROFILE,{'title':item['title'],'content':item['content'],'kind':'text','active':True,'agent_id':AGENT})
        sources.append(source)
        chunks=sandbox.execute('SELECT content FROM saas_ai_knowledge_chunks WHERE source_id=%s::uuid ORDER BY chunk_no',(source['id'],))
        assert [c['content'] for c in chunks]==chunk_text(item['content'])
    sandbox.commit()
    return sandbox,sources,provider


def retrieve(query):
    item=store.reserve(PROFILE,new(),message(query))
    store.finish(item,attempted=False)
    return item['extra']['knowledge']


@pytest.mark.parametrize('query',[
    'Nəsə maldan çatışmayan olsa burda sayımda proqramda göstərirdə?',
    'Sayimda catismayan mallari gore bilerem?',
    'Sayım fərqi sənədi keçirmədən görünür?',
    'Inventarizasiyada faktiki qalıqdan fərq çıxanda nə olur?',
    'Anbar qalığı artıq və ya əskik olanda necə göstərilir?',
    'Sayımda müsbət və mənfi fərq ola bilər?',
    'Sayim ferqi musbet ve menfi olur?',
    'Где увидеть недостачу при инвентаризации?',
    'Где показаны расхождения после пересчёта?',
    'İNVENTARİZASİYADA FƏRQ GÖRÜNÜR?',
    'Покажет ли инвентаризация положительную и отрицательную разницу?',
    'Как увидеть излишки и нехватку товара на складе?',
    'Can stock counting show a positive or negative difference?',
])
def test_inventory_paraphrases_retrieve_published_facts(indexed,query):
    db,sources,provider=indexed
    rows=retrieve(query)
    assert rows and {r['source_id'] for r in rows}<={s['id'] for s in sources}
    assert any('fərq' in r['content'] for r in rows)
    assert len(rows)<=6 and all(len(r['content'])<=1800 for r in rows)
    provider.assert_not_called()


@pytest.mark.parametrize('query',[
    'Sayımı yadda saxlayıb növbəti gün davam edə bilərəm?',
    'Sayimi sabah davam etdire bilerem?',
    'Как сохранить документ инвентаризации и продолжить завтра?',
    'Можно сохранить и продолжить на следующий день?',
    'Can I save inventory counting and resume the next day?',
])
def test_save_continue_paraphrases_include_full_instruction(indexed,query):
    db,sources,provider=indexed
    rows=retrieve(query)
    assert sources[1]['id'] in {r['source_id'] for r in rows}
    assert any('növbəti gün' in r['content'] for r in rows)
    provider.assert_not_called()


@pytest.mark.parametrize('query',[
    'Sabah hava necə olacaq?',
    'What different payment methods do you offer?',
    'Как настроить интернет роутер?',
    'Telefon nömrəsini necə dəyişmək olar?',
    'Şifrəni necə saxlamaq olar?',
    'Как сохранить пароль?',
    'Положительный анализ крови что означает?',
    'Müsbət analiz nə deməkdir?',
    'What is a negative blood test?',
    'Proqramda nə var və necə olar?',
])
def test_unrelated_queries_do_not_get_inventory_chunks(indexed,query):
    assert retrieve(query)==[]
    indexed[2].assert_not_called()


def test_retrieval_keeps_tenant_agent_active_filters_and_source_identity(indexed):
    db,sources,provider=indexed
    other=uid()
    db.execute("INSERT INTO saas_ai_agents(id,tenant_id,name,created_by) VALUES(%s::uuid,%s::uuid,'Other',11)",(other,A));db.commit()
    content=PUBLISHED[0]['content']
    excluded=[]
    for profile,agent,active in [(PROFILE,other,True),(PROFILE,AGENT,False),({'tenant_id':B,'telegram_id':22},None,True)]:
        s=agents.save_knowledge(profile,{'title':'Excluded','kind':'text','content':content,'agent_id':agent,'active':active});excluded.append(s['id'])
    global_source=agents.save_knowledge(PROFILE,{'title':'Shared company facts','kind':'text','content':content,'active':True})
    for _ in range(8):agents.save_knowledge(PROFILE,{'title':'Relevant copy','kind':'text','content':content,'active':True,'agent_id':AGENT})
    rows=retrieve('Sayım fərqi')
    assert len(rows)==6
    assert not set(excluded)&{r['source_id'] for r in rows}
    # Shared company knowledge remains eligible under the existing agent filter.
    db.execute('UPDATE saas_ai_knowledge_sources SET active=FALSE WHERE tenant_id=%s::uuid AND agent_id=%s::uuid',(A,AGENT));db.commit()
    assert {r['source_id'] for r in retrieve('Sayım fərqi')}=={global_source['id']}
    provider.assert_not_called()


def test_worker_keeps_unknown_az_suffix_fallback_and_scope_filters(indexed,monkeypatch):
    import tenant_ai_agent_run_store as runs
    db,_,provider=indexed
    monkeypatch.setattr(runs,'_connect',lambda:db)
    source=agents.save_knowledge(PROFILE,{'title':'Registration help','kind':'text',
        'content':'Qeydiyyatdan sonra hesaba daxil olun.','active':True,'agent_id':AGENT})
    rows=runs.knowledge_context(A,'qeydiyyat',agent_id=AGENT)
    assert {row['source_id'] for row in rows}=={source['id']}
    assert not runs.knowledge_context(B,'qeydiyyat',agent_id=AGENT)
    assert not runs.knowledge_context(A,'qeydiyyat',agent_id=uid())
    assert not runs.knowledge_context(A,'What different payment methods do you offer?',agent_id=AGENT)
    provider.assert_not_called()
