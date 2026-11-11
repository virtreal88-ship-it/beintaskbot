# Employee card scopes

The global `TENANT_SCOPED_ACCESS_ENABLED` flag remains off by default. Employee matrix cells expose implemented `view`, `create` and `update`. Self means the employee's own tenant membership; department compares current database department IDs within the same company. Null departments grant no department scope. An absent cell preserves the former `employees:manage` admission; explicit none denies that action.

GET `/api/platform/members` and `/api/platform/me` member previews use the same filtered list. There is no employee-list pagination/count API in this version; all returned identity, role, contact, WhatsApp and editability fields belong only to rows selected by the scoped SQL. The list reads current membership/workflow while holding the shared tenant/policy/membership fences. Department moves use the existing exclusive tenant fence. Writes lock tenant, policies and actor/target memberships and re-evaluate independent update scope. Legacy upsert, deactivation and number mutation entry points use those same fences.

The UI uses separate read/update flags and row-level `editable_fields`. Read-only users can open cards without receiving editable controls; creation uses its independent C flag; invitations and access-request tabs remain behind the coarse management flag. Access revocation clears cards and closes the editor. Existing owner/admin target protections and owner-only account-credential/profile assignment paths remain authoritative.

Non-owner self update retains only the previously permitted WhatsApp recognition-number edits. Own role, status, display alias, permissions, department and access settings are not newly editable. The existing password-change route remains separate from employee administration.

## Deliberately unresolved operations

There is no employee deletion operation. Deactivation/reactivation is a membership update, not a fabricated delete grant. Creation/provisioning now accepts a validated `department_id`. Explicit C=department requires the creator's current, active department and rejects omitted, foreign, archived or different departments. C=all accepts any active department in this company and retains creation without a department. C=self is not applicable: validation rejects it, malformed stored values fail closed, and the per-action editor omits it. Sparse C preserves legacy creation admission, but cannot delegate unrestricted versions of other scoped or Boolean-restricted grants. Gate-off preserves the legacy grant-subset rule.

`GET /api/platform/members?options=create` checks fresh creation authority and returns only permitted active departments plus assignable permission keys, never employee cards. A C-only user can create and receive an opaque receipt without gaining R. Provisioning creates a new account only; credential changes to existing accounts retain owner-only checks. Scoped non-owner creators cannot assign owner/admin roles, grants they do not hold, or unrestricted versions of rights restricted by their scopes/native Boolean overrides. Existing-target direct upsert still requires U and cannot move departments; use the versioned owner-protected card operation for such moves. Invitation workflows remain on the legacy coarse guard and are not silently upgraded to scoped creation.

Creation holds tenant, policy and actor membership locks and the selected department lock through commit. Current department/status, role and grants are rechecked in the write transaction; option lists are not authorization. No deletion operation is introduced.

## Verification

- `tests/test_employee_card_scopes.py`: four levels, independent read/update, two departments/tenants, sparse and gate-off behavior, protected self fields, SQL parameters and capabilities.
- `tests/test_employee_card_scopes_postgres.py`: real filtered identity lists, department moves, revocation, write fences, denied self privilege edits and independent update with no read. Requires disposable localhost PostgreSQL.
- `tests/test_employee_card_scopes_ui.cjs`: actual app read-only card, hidden management controls, revocation cleanup and zero writes.

- `tests/test_employee_creation_scopes.py`: creation levels, unsupported self, sparse/gate-off behavior, missing/archived department and grant escalation guards.
- `tests/test_employee_creation_scopes_postgres.py`: actual memberships and HTTP provisioning, C without R, foreign departments, revocation, and lock contention through commit.
- `tests/test_employee_creation_scopes_ui.cjs`: RU/AZ/EN per-action choices, C-only form/receipt, repeated submit and stale options after revocation.

## Grant attenuation

With the gate enabled, sparse C retains admission but not a bypass around other restrictions. New grants are evaluated across their effective domains, including customers → companies, ordinary role defaults, HotOrderPolicy create/claim and approval, task/deal approval, task markers and pipeline/stage sets. Explicit All can delegate equivalent grants when underlying provider/domain restrictions are not narrower. Source/member settings are never implicitly copied. Non-owner creation cannot revive orphan per-member policy records without owner review. Existing-card U checks added grants and role-based authority expansion while retaining unrelated existing rights; the scoped matrix writer remains owner-only. Gate-off retains the original grant-subset behavior.
