import pytest
import uuid
from tenant_linear_policy import TenantLinearError
import tenant_operator_voice_source_snapshot as snap
from tenant_operator_voice_orchestration import CopilotContext


def test_seal_matches_canonical_values_and_detects_backfilled_unselected_id():
    scope={'tenant':'t','actor':7,'channel_id':'c','lead_id':1}
    rows=[{'external_id':'newer','body':'synthetic'}];projection={'lines':[{'text':'synthetic'}]}
    records=[{'row_id':str(uuid.uuid4()),'external_id':'newer','record':{'body':'synthetic'}}]
    first=snap.seal(scope,projection,rows,[],['newer'],{},records)
    same=snap.seal(dict(reversed(list(scope.items()))),projection,rows,[],['newer'],{},records)
    backfill=snap.seal(scope,projection,rows,[],['newer','older-backfill'],{},records)
    assert first.matches(same) and not first.matches(backfill)
    assert 'synthetic' not in repr(first)


@pytest.mark.parametrize('rows,ids',[
    ([{'external_id':'a'},{'external_id':'a'}],['a']),
    ([{'external_id':''}],['a']),([{'external_id':'a'}],['different']),
    ([{'external_id':'a'}],['a','a']),([{'external_id':'a'}],['a']*5001),
])
def test_ambiguous_or_unbounded_sources_refused(rows,ids):
    with pytest.raises(TenantLinearError):snap.seal({}, {},rows,[],ids,{},[])


def test_noncanonical_values_fail_closed():
    records=[{'row_id':str(uuid.uuid4()),'external_id':'a','record':{}}]
    with pytest.raises(TenantLinearError):snap.seal({}, {'x':float('nan')},[{'external_id':'a'}],[],['a'],{},records)


def test_inconsistent_double_capture_refused():
    ctx=CopilotContext(None)
    records=[{'row_id':str(uuid.uuid4()),'external_id':'a','record':{}}]
    first=snap.seal({}, {'v':1},[{'external_id':'a'}],[],['a'],{},records)
    second=snap.seal({}, {'v':2},[{'external_id':'a'}],[],['a'],{},records)
    values=iter([({'v':1},first),({'v':2},second)])
    ctx._capture_once=lambda:next(values)
    with pytest.raises(TenantLinearError):ctx._capture_stable()
