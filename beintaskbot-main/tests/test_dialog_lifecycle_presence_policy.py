import pytest

from tenant_dialog_presence import ROUTING_ENV, is_online


A = "00000000-0000-0000-0000-000000000101"
B = "00000000-0000-0000-0000-000000000102"


class Cursor:
    def __init__(self, *rows):
        self.results = iter(rows)
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))

    def fetchone(self):
        return next(self.results)


@pytest.mark.parametrize(("workspace", "flag", "tenant"), [
    (A, None, A), (A, "false", A), (A, "invalid", A),
    (A, "true", B), ("invalid", "true", A), (None, "true", A),
])
def test_legacy_admission_does_not_query_presence_schema(monkeypatch, workspace, flag, tenant):
    for key, value in (("BEINSYSTEMS_OPERATOR_TENANT_ID", workspace), (ROUTING_ENV, flag)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    cur = Cursor()
    assert is_online(cur, tenant, 11, lock=True)
    assert cur.statements == []


def test_requested_routing_before_migration_reads_only_the_catalog(monkeypatch):
    monkeypatch.setenv("BEINSYSTEMS_OPERATOR_TENANT_ID", A)
    monkeypatch.setenv(ROUTING_ENV, "true")
    cur = Cursor({"table_name": None})
    assert is_online(cur, A, 11, lock=True)
    assert len(cur.statements) == 1 and "to_regclass" in cur.statements[0][0]


@pytest.mark.parametrize(("status", "eligible"), [
    (None, False), ("away", False), ("lunch", False), ("online", True),
])
def test_enforcement_requires_an_explicit_online_row_under_membership_lock(monkeypatch, status, eligible):
    monkeypatch.setenv("BEINSYSTEMS_OPERATOR_TENANT_ID", A)
    monkeypatch.setenv(ROUTING_ENV, "true")
    cur = Cursor({"table_name": "saas_dialog_employee_presence"}, {"active": 1},
                 {"status": status} if status else None)
    assert is_online(cur, A, 11, lock=True) is eligible
    membership, presence = cur.statements[1:]
    assert "saas_tenant_members" in membership[0] and "FOR SHARE" in membership[0]
    assert "saas_dialog_employee_presence" in presence[0] and "FOR SHARE" in presence[0]
    assert membership[1] == presence[1] == (A, 11)
