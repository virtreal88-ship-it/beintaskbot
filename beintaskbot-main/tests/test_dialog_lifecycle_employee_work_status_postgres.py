"""Real signed sessions and role fences for local employee work status."""
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from tenant_browser_session import COOKIE_NAME
from tenant_employee_work_status_api import handler as profile_status
from tenant_operator_api import handler as operator
import tenant_dialog_presence as presence
from test_company_employees_navigation_postgres import staff, database, db, A, B, LOGGER, ORIGIN, _apis, _get

PROFILE = "/api/platform/profile/availability"
DIALOGS = "/api/platform/dialogs/presence"
MEMBERS = "/api/platform/members"


def identity(tenant, actor):
    return {"expected_tenant_id": tenant, "expected_user_id": str(actor)}


@pytest.mark.asyncio
async def test_shared_profile_and_owner_roster_have_independent_exact_acl(staff, monkeypatch):
    f = staff
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV, "true")
    monkeypatch.delenv(presence.ROUTING_ENV, raising=False)
    monkeypatch.setenv("BEINSYSTEMS_OPERATOR_TENANT_ID", A)
    _, employee = _apis(f)
    auth = f["session"].profile
    app = web.Application()
    app.router.add_route("GET", PROFILE, profile_status(auth, ORIGIN, LOGGER))
    app.router.add_route("POST", PROFILE, profile_status(auth, ORIGIN, LOGGER))
    app.router.add_route("GET", DIALOGS, operator(auth, ORIGIN, LOGGER, "presence"))
    app.router.add_route("POST", DIALOGS, operator(auth, ORIGIN, LOGGER, "presence"))
    app.router.add_get(MEMBERS, employee["members"])
    token = {actor: f["session"].issue(A, actor) for actor in (11, 31, 32, 35, 37, 41)}
    foreign_token = f["session"].issue(B, 37)
    owner_b = f["session"].issue(B, 22)
    before_acl = f["db"].execute("""SELECT tenant_id,telegram_id,role,permissions,active
      FROM saas_tenant_members ORDER BY tenant_id,telegram_id""")
    f["db"].commit()
    async with TestClient(TestServer(app)) as http:
        async def read(path, actor, **extra):
            return await _get(http, path, token[actor], params={**identity(A, actor), **extra})

        async def save(actor, data, *, route=PROFILE, origin=ORIGIN):
            response = await http.post(route, headers={"Cookie": COOKIE_NAME+"="+token[actor], "Origin": origin}, json=data)
            return response.status, await response.json()

        assert (await read(PROFILE, 41))[1]["result"]["available"] is False
        assert (await save(41, {**identity(A,41),"status":"offline"}))[0] == 409
        with f["factory"]() as conn, conn.cursor() as cur:
            presence.migrate(cur)
            assert not presence.status_schema_ready(cur, require_offline=True)
        assert (await read(PROFILE, 41))[1]["result"]["available"] is False
        with f["factory"]() as conn, conn.cursor() as cur:
            assert not presence.provision_status_schema(cur)
            assert not presence.provision_status_schema(cur)
            assert presence.status_schema_ready(cur, require_offline=True)
            cur.execute("SELECT count(*) AS n FROM saas_dialog_employee_presence")
            assert cur.fetchone()["n"] == 0

        status, payload, cache = await read(PROFILE, 41)
        assert status == 200 and cache["Cache-Control"] == "no-store"
        assert payload["result"] == {"available":True,"status":"online","configured":False,
            "can_update":True,"routing_enforced":False,"can_set_offline":True}
        assert (await read(DIALOGS,41))[0] == 403
        assert (await read(PROFILE,41,expected_tenant_id=B))[0] == 409
        assert (await read(PROFILE,41,expected_user_id=11))[0] == 409
        assert (await read(PROFILE,41,employee_id=11))[0] == 400
        assert (await _get(http, PROFILE, params=identity(A,41)))[0] == 403
        assert (await save(41,{**identity(A,41),"status":"offline","employee_id":11}))[0] == 400
        assert (await save(41,{**identity(A,41),"status":"offline"},origin="https://foreign.test"))[0] == 403
        assert (await save(41,{**identity(B,41),"status":"offline"}))[0] == 409
        for state in ("online","away","lunch","offline"):
            assert (await save(41,{**identity(A,41),"status":state}))[1]["result"]["status"] == state
        assert (await save(41,{**identity(A,41),"status":"online"},route=DIALOGS))[0] == 403
        assert (await read(DIALOGS,41))[0] == 403
        with f["factory"]() as conn:
            conn.execute("UPDATE saas_dialog_employee_presence SET updated_at='2020-01-01' WHERE tenant_id=%s AND employee_id=41", (A,))
        renewed = f["session"].issue(A,41)
        assert (await _get(http,PROFILE,renewed,params=identity(A,41)))[1]["result"]["status"] == "offline"
        # The same user identity belongs to two workspaces, with isolated states.
        assert (await save(37,{**identity(A,37),"status":"away"}))[0] == 200
        assert (await _get(http,PROFILE,foreign_token,params=identity(B,37)))[1]["result"]["status"] == "online"
        assert (await read(MEMBERS,11,include="work-status",expected_tenant_id=B))[0] == 409
        default = (await read(MEMBERS,11))[1]["members"]
        assert not any("work_status" in row for row in default)
        allowed = (await read(MEMBERS,11,include="work-status"))[1]["members"]
        by_id = {row["telegram_id"]:row for row in allowed}
        assert by_id[41]["work_status"] == "offline"
        assert by_id[37]["work_status"] == "away"
        assert by_id[32]["work_status"] == "online"
        assert by_id[41]["card_version"] == next(row["card_version"] for row in default if row["telegram_id"]==41)
        assert all(row["tenant_id"]==A for row in allowed)
        for actor in (31,35):
            baseline,_,_ = await read(MEMBERS,actor)
            code, result, _ = await read(MEMBERS,actor,include="work-status")
            assert code == baseline
            if actor == 31:
                assert code == 200, "existing employee manager still sees the list"
            if code == 200:
                assert not any("work_status" in row for row in result["members"])
            else:
                assert code == 403 and "members" not in result
        other = (await _get(http,MEMBERS,owner_b,params={**identity(B,22),"include":"work-status"}))[1]["members"]
        assert next(row for row in other if row["telegram_id"]==37)["work_status"] == "online"
        assert all(row["tenant_id"]==B for row in other)

        # Profile and Dialogues are two views of one choice for an entitled operator.
        with f["factory"]() as conn:
            conn.execute("""UPDATE saas_tenant_members SET permissions='["dialogs"]'::jsonb
              WHERE tenant_id=%s AND telegram_id=32""", (A,))
        assert (await save(32,{**identity(A,32),"status":"offline"}))[0] == 200
        assert (await read(DIALOGS,32))[1]["result"]["status"] == "offline"
        assert (await save(32,{**identity(A,32),"status":"lunch"},route=DIALOGS))[0] == 200
        assert (await read(PROFILE,32))[1]["result"]["status"] == "lunch"
        with f["factory"]() as conn:
            conn.execute("""UPDATE saas_tenant_members SET permissions='["tasks"]'::jsonb
              WHERE tenant_id=%s AND telegram_id=32""", (A,))
        assert f["db"].execute("""SELECT tenant_id,telegram_id,role,permissions,active
          FROM saas_tenant_members ORDER BY tenant_id,telegram_id""") == before_acl
        f["db"].commit()
        with f["factory"]() as conn:
            conn.execute("UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=41", (A,))
        assert (await read(PROFILE,41))[0] == 403
        after = (await read(MEMBERS,11,include="work-status"))[1]["members"]
        assert "work_status" not in next(row for row in after if row["telegram_id"]==41)
        monkeypatch.delenv(presence.STATUS_ENABLED_ENV)
        assert (await read(PROFILE,32))[1]["result"]["available"] is False
        assert not any("work_status" in row for row in (await read(MEMBERS,11,include="work-status"))[1]["members"])
        with f["factory"]() as conn:
            assert conn.execute("SELECT status FROM saas_dialog_employee_presence WHERE tenant_id=%s AND employee_id=41",(A,)).fetchone()["status"] == "offline"
