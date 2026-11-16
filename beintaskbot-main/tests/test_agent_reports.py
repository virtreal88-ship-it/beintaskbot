import uuid
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, AGENT, PROFILE, seed_job
from test_tenant_night_report import night_db, event, NOW
from test_night_report_scopes import scoped_night, grants, PERSON
from tenant_linear_policy import TenantLinearError
import tenant_agent_reports as reports


def read(db, monkeypatch, profile=PROFILE, **kw):
    monkeypatch.setattr(reports,'_connect',lambda:db)
    return reports.report(profile,first='2026-10-15',last='2026-10-15',zone='UTC',now=NOW,**kw)


def run(db, fingerprint, agent=AGENT, state='completed', channel=CA):
    event(db,fingerprint,channel)
    db.execute('''INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,created_at)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,1,'test',%s,'2026-10-15T21:00Z')''',
      (A,str(uuid.uuid4()),agent,fingerprint,state))


def test_actual_agent_id_not_current_assignment(night_db,monkeypatch):
    db=night_db;seed_job(db)
    second=str(uuid.UUID(int=888))
    db.execute('''INSERT INTO saas_ai_agents(id,tenant_id,name,created_by) VALUES(%s::uuid,%s::uuid,'Day agent',11)''',(second,A))
    run(db,'first');run(db,'second',second,state='failed');db.commit()
    result=read(db,monkeypatch,agent_id=second)
    assert result['facts']==[{'agent_id':second,'state':'failed','count':1,'conversations':1}]
    assert result['unavailable_metrics']
    with pytest.raises(TenantLinearError) as error:read(db,monkeypatch,agent_id=str(uuid.UUID(int=999)))
    assert error.value.status==404


def test_scope_restricts_runs_before_aggregate(scoped_night,monkeypatch):
    db=scoped_night[0];grants(db,'self')
    result=read(db,monkeypatch,PERSON)
    assert sum(row['count'] for row in result['facts'])==1
    assert len(result['agents'])==1
    version=result['access_version']
    grants(db,'none')
    with pytest.raises(TenantLinearError):read(db,monkeypatch,PERSON,expected_access_version=version)


def test_daytime_not_hardcoded_night():
    result=reports.period('2026-10-15','2026-10-15','Asia/Baku',now=NOW)
    assert result['starts_at']=='2026-10-14T20:00:00+00:00'
    assert result['ends_at']=='2026-10-15T20:00:00+00:00'
    for first,last,zone in [('2026-01-01','2026-03-01','UTC'),('2026-01-03','2026-01-01','UTC'),('2026-01-01','2026-01-01','invalid')]:
        with pytest.raises(TenantLinearError):reports.period(first,last,zone)
