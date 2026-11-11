# AI and integration scope audit

This describes implementation boundaries, not production configuration or release approval. The rollout gate remains off by default. No real rights, credentials, connections, customer messages or imports were changed by this work.

## Historical preparation

The initial audit added hidden AI `access_responsible_id` ownership, tenant-bound foreign keys, readiness checks and an idempotent migration receipt. Historical ownership was backfilled only for an unambiguous active company owner; new user-created agents use the authenticated employee, and browser patches cannot reassign ownership. That checkpoint deliberately exposed no AI matrix or UI. It has since been replaced by the limited document feature below; the earlier absence of a configurable row is no longer the current state.

## Shared integration management

The earlier undefined-ownership blocker is resolved: the current active company
owner is responsible for each company-wide connection. Current-database
self/department/all scopes apply separately to management reads, creation,
updates, and deletion. Safe connection state remains visible to active members;
credentials and arbitrary metadata are never part of that projection.

Disconnect and key replacement require a company-impact confirmation. Provider
constraints and stronger owner-only operations remain in force, including OAuth,
Linear employee/publication/runtime rules, and protected Chat AI model settings.
This is local implementation, not rollout permission or evidence of deployment.
See `shared-integration-scopes.md` and its isolated tests for exact coverage.

## Current limited AI document feature (C/R/U and unused archive D)

This feature covers **agent documents only**, not the entire AI administration surface. `ai_agents` registers view/create/update/delete. Employee D means only archive of an unused, unbound agent, not permanent deletion. The global rollout gate is still off by default; no production settings or rights were changed.

- Separate `ai_documents` capabilities expose action-specific navigation while the coarse `modules.ai_agents` management capability remains false for scoped actors. Shared AI applications therefore do not become accessible through a document grant. A dedicated scoped console loads only the authorized agent list and local source-bound editor options. It supports create, edit, source/model/mode selection and enable/disable, with per-document update eligibility. Create-only users never request the list; read-only users never load editing catalogs. Stale identity responses and repeated saves are fenced, and Cancel closes the editor without writing.
- Create always produces a disabled agent for scoped actors; activation is a subsequent update. All scoped mutation responses contain only id/version, including retries and trigger writes. Updating without read permission cannot reveal old instructions, policies or bindings.
- Agent reads use an explicit field whitelist. Response policies, helper bindings, shared tool configuration and dialog totals are omitted. Sources are included only after current source-view authorization; update eligibility additionally requires source update rights. Trigger reads whitelist identity/name/type/status/version fields and omit arbitrary nested actions/conditions.
- Scoped options use only local catalogs, never provider refresh or job-derived channel identifiers. Connect choices require current source view and update rights. Kommo choices additionally require the current pipeline/stage grant; unverified Kommo channel identifiers are rejected. Writes recheck both existing and resulting sources under the same transaction. Configuration-triggered reconciliation is restricted to authorized resulting Connect channels, never a tenant-wide sweep.
- Trigger create/update/delete is parent-agent U, not generic agent C/D. Both the persisted and resulting trigger contracts are checked for updates, and the persisted contract is locked and checked before deletion. Only handoff/handoff-after-reply actions and phrase conditions are permitted; cross-agent execution and native/Kommo CRM effects require separate existing management. Configurations with shared expert tools, delegation or shared dispatch remain management-only.
- Operator assignment/recovery, employee case-learning bypasses, and dialog projections no longer restore broad agent identities/configuration to scoped document users. Shared group/candidate enrichment is skipped; agent filters are rejected, agent metadata is removed, and shared AI controls are disabled. Ordinary authorized dialogue/message reads remain available. The support worker's separate ticket projection filtering must also be retained during integration.
- Independent integration/Chat AI settings permissions remain separate and unchanged. This matrix does not create or revoke provider-configuration authority. The scoped editor does not depend on those management endpoints.

Shared knowledge, team management, diagnostic dashboards, simulations, historical replay, paid tests, instructor/learning and administrator workflows are not document CRUD and remain behind their existing owner/unscoped-management boundaries. No scoped user is newly admitted to personal test/learning jobs. Company runtime execution continues under its existing company/agent runtime safety policy; this change does not retroactively cancel company runtime when an employee loses editor rights. The approved employee D operation archives only unused/unbound agents. Existing archive/transfer and permanent deletion remain owner-only.

Verification adds a real JSDOM test (`tests/test_ai_scoped_documents_ui.cjs`), strict source/pipeline and receipt/projection backend regressions, and real PostgreSQL concurrency tests collected for CI. Local PostgreSQL tests remain skipped without an isolated DSN. Full Chromium and concurrent PostgreSQL release validation remain integration-CI responsibilities.


## Transaction and revocation boundaries

Current membership, policy, responsible member, department and source grants are checked from the database in the operation's transaction. Cached browser roles and grants do not authorize access. Scoped updates acquire the existing tenant configuration advisory mutex before taking the agent row `FOR UPDATE`, avoiding shared-lock upgrades and lock inversion against wizard recovery. Read-only setup retries use `FOR SHARE`; recovery locks candidates `FOR UPDATE` before writing the setup key.

Scoped trigger deletion takes the parent update boundary, then locks the exact tenant/agent/trigger row `FOR UPDATE` and checks its persisted actions and condition. A shared CRM, native task or helper trigger cannot be deleted or rewritten into a safe-looking handoff through scoped U. Existing owner and gate-off legacy paths retain their authority.

Employee revocation stops subsequent editor operations; it does not retroactively cancel company runtime. Already-admitted company runtime continues under existing runtime policy and safety controls. Shared paid test/learning workflows are not newly admitted through these document grants.

## Remaining decisions and release checks

- **Employee D is narrowly implemented as unused-agent archive.** Existing archive/transfer and permanent deletion remain owner-only. Bound or historically used agents fail closed rather than moving or rewriting data.
- Shared knowledge, teams, diagnostics, simulation, replay, paid tests, instructor/learning and administrator workflows are not covered by document C/R/U. Their owner/unscoped-management boundaries must remain intact. This is not a claim of complete AI CRUD coverage.
- Preserve independent integration/provider permissions and the separate support/employee guards when combining changes. Source rights must not become credential-management authority, and a document grant must not become manage-all.
- Keep the global gate off until combined synthetic comparisons, full release tests and rollout review are complete.

## Verification evidence

The local isolated backend regression command covers `test_ai_scoped_documents.py`, `test_ai_scoped_documents_postgres.py`, `test_ai_integration_access_preparation.py`, `test_tenant_policy.py`, `test_tenant_scoped_access.py`, `test_analytics_scoped_dialog_guard.py`, `test_business_analyst_scoped_boundary.py`, `test_dialog_mutation_source_scopes.py` and `test_tenant_ai_agents_api_diagnostics.py`. Before the persisted-trigger fix this run passed 126 tests and 10 subtests; 4 real PostgreSQL concurrency tests were skipped locally because no disposable DSN was available. The follow-up adds four parameterized persisted-trigger regressions covering delete and rewrite denial, unchanged rows/audit state, and retained owner deletion.

The real PostgreSQL suite checks competing updates, configuration-mutex/row ordering, revocation before admission and admitted-update fencing. It uses `pg_blocking_pids` observations and requires a disposable localhost `BEINTASKBOT_TEST_POSTGRES_DSN`; PGlite is not a substitute for these races.

The real JSDOM suite `tests/test_ai_scoped_documents_ui.cjs` checks independent C/R/U, no shared fetch dependencies, duplicate submission, Cancel, stale tenant responses and revocation. Existing `test_tenant_ai_agent_connect_scope_ui.cjs` and `test_tenant_chat_ai_ui.cjs` also passed locally. Full Chromium and concurrent PostgreSQL release results belong to the combined integration CI report, not this local audit.

### Browser acceptance boundary

`tests/test_ai_scoped_documents_browser.py` launches Chromium against localhost
with the production HTML DOM and the complete `tenant-ai-agents.js` controller.
Other app controllers and external resources are omitted. Its HTTP responses
are deliberately synthetic: it checks independent C/R/U rendering, C-only no
list fetch, per-row edit controls, source-denial handling, minimal receipt
handling, 404 revocation cleanup, stale-identity response rejection, and no
shared API dependencies. It does **not** prove cookie authentication or database
authorization; the backend scope and PostgreSQL suites supply separate evidence.
The browser assertions dispatch actual DOM events, not native pointer input.
Run with `python -m pytest tests/test_ai_scoped_documents_browser.py`; Chromium
must be installed and allowed to create local sockets. A missing executable
skips; a failed browser launch or assertion fails. This environment's sandbox
blocked Chromium's singleton socket, so local execution is not a browser pass.

### Unused-agent archive D

`POST /agents/{id}/archive-unused` requires current independent D ownership
scope and an exact integer version. It does not require R, expose a dependency
preview, or return document content. D-only UI does not enumerate agents; a
readable card can offer Archive only when its independent D scope permits it.
Enabled agents, source-bound agents, outgoing helper bindings, and all registered
operational or historical references block this operation. Creation/configuration
audit and identity tombstones are not use and are preserved. No references are
transferred, cancelled, deleted or rewritten.

`tenant_ai_unused_archive.REFERENCES` is the shared blocker/admission registry.
It covers direct agent IDs, reverse helper policies, trigger/group bindings,
queued/running/historical work, knowledge/test/campaign records, instructor and
simulation records, OAuth/owner grants, training drafts/rollbacks, retained
dialogue snapshots and nested webhook execution bindings. Structural JSON keys
are inspected recursively; prompt/message prose and assignment receipt UUIDs
are excluded. Optional schema creators install the same reference fence before
first writes. Adding a new relationship requires extending this registry/hook.

Archive holds agent UPDATE, while new reference admission holds agent SHARE.
Previously admitted references make archive fail; waiting reference creation
sees the committed scoped-archive marker and fails. Updates preserving the same
reference set do not acquire fresh agent locks, avoiding lock inversion with
existing worker/owner cleanup. Existing owner archive behavior is unchanged.

Tests: `test_ai_unused_archive.py` checks scopes, version/current authority,
legacy/gate-off boundaries, generic denial receipts, outgoing/incoming blockers,
structural extraction and optional-table fences. `test_ai_unused_archive_postgres.py`
checks both JSON-association race orders and concurrent membership revocation
using actual independent PostgreSQL connections. DOM tests cover per-row D and
D-only no-list behavior. PostgreSQL race tests require the isolated CI DSN.

Compatibility is marker-specific: existing owner archive rows and permanent-delete
identity tombstones do not acquire a scoped-archive marker. Their legitimate
historical writes remain allowed, including with the rollout gate off. Only a
row archived through the new unused operation rejects new references, even if
the rollout gate is subsequently disabled. Schema readiness checks helper
signatures and exact enabled trigger/function/arguments for every present
registered relation; disabled, missing or stale fences require migration repair.
Do not enable the feature during a mixed-version rollout: old processes could
create an optional table without its new hook. All workers must run the updated
code before the global gate can be enabled.
