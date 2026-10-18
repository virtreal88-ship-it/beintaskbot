# BeinSystems internal instructor — connection contract, not a working pilot

Base: main 35886f54. Scope: inventory.warehouse_transfer only.
Status: integration_unavailable. No live browser operation, AI run, screenshots,
verified instruction, demo-data modification or knowledge publication was performed.
No existing agent configuration or production integration was inspected for credentials.

## Findings and access boundary

- tenant_ai_expert_service.py implements call_expert, reserve/execute/_settle, existing
  AI Teams principal permissions, per-specialist knowledge/model and company quotas.
  _live checks leased parent task, Human/Pause state and group_revision; _settle rechecks.
- tenant_ai_expert_policy.py TOOLS is a read-only diagnostic allowlist. Browser access
  is NOT registered. tenant_ai_expert_tools.py explicitly has no live BeinSystems adapter.
- tenant_bein_api.py / tenant_bein_integration.py connect a tenant production ERP API
  account. This is not evidence of a safe demonstration company or a browser sandbox.
  docs/crm-provider-architecture.md documents deals/tasks/customers/pipelines/stages;
  it does not establish inventory-transfer UI routes, selectors or screenshot capability.
- tenant_browser_session.py authenticates Beintaskbot cookies; it is not an isolated
  browser automation provider.
- Available desktop automation: Chrome extension and in-app browser. Inventory showed
  Beintaskbot/Connect/Railway/Kommo tabs, no identified BeinSystems training application.
  Those user-profile browser sessions are unsuitable as a bot's isolated demo runtime.
- No verified demo URL/company, restricted account, synthetic fixture set, demonstrated
  restore procedure or approved UI map was supplied or found in the scoped source review.
  This does NOT establish that no training company exists; its safety/access is unconfirmed.

Accordingly, work stops at contract/architecture, as requested. There are no fabricated
screenshots, placeholder inventory instructions or browser mocks presented as a real pilot.

## Connection prerequisites (owner / BeinSystems administrator)

Provide a separate training application origin and company identity, a dedicated inventory
account restricted to that company, and synthetic product/source/destination warehouse
fixtures. Verify the identity banner/company ID server-side, and deny company switching,
real tenants, billing, user management, integrations, exports, credentials and critical settings.
Do not paste passwords/cookies/tokens into prompts. Store the account in the existing encrypted
secret infrastructure or an approved vault, referenced by opaque server-only connection_ref.
The existing production ERP integration token MUST NOT be used as fallback.

An administrator must demonstrate restoring the synthetic fixture snapshot in that training
company and record a restore receipt. A task must not reset data itself. Without restore
verification, even demo transfer creation is unavailable. Safety attestations/restore receipts
must be independently verified server-side, not accepted as user/model boolean assertions.
No new paid service is provisioned by this change.

## Narrow adapter contract

See tenant_ai_instructor_contract.py. It defines frozen connection/task/action/artifact
shapes, validation helpers and DemoBrowserAdapter Protocol, with NO implementation or
runtime registration. availability() always returns integration_unavailable, verified:false,
no screenshots and customer_send_allowed:false. Contract tests validate shapes only.

Future server adapter operations: open_demo, observe, navigate, click, fill_demo, screenshot,
close. Actions use a reviewed semantic UI map, not arbitrary URLs, JavaScript, selectors,
shell, SQL, file uploads/downloads or callbacks supplied by the model. Map names in the
contract are requested capabilities, NOT observed labels, routes or a verified step sequence.
Demo inputs are fixture references only; resolve them server-side to synthetic values. The
only prospective write is save_demo_transfer inside the attested demo company. No production
write capability is added. External links, redirects, popups and non-approved requests must
be blocked; enforce exact origin plus DNS/IP/egress checks (including DNS rebinding), not
merely a string URL validator. Explicitly deny production origins even if cookies are absent.

Every task gets a new isolated ephemeral browser context with no imported user-profile
cookies/storage. A tenant/task lock prevents concurrent writes to the same fixture set.
Verify company/account identity after login, after navigation and immediately before writes.
Recheck the live primary lease/agent permissions/Human Mode/group revision before each action;
cancel and close when authority changes. Enforce cumulative wall-clock/actions/token budgets,
not per-call resettable counters. Ceilings: min(existing expert budget,30s), 20 actions,
12000 reserved tokens, six captures; provider calls retain existing accounting. If the
operation cannot finish inside the existing expert deadline, return not_verified; do not
increase worker limits implicitly. Browser failures/timeouts close sessions and leave uncertain
writes unavailable for replay until fixture status is checked/restored by an administrator.

Screenshots must come from actual browser capture. A private artifact service must crop/mask
identity, account details, notifications, tokens, passwords and PII before persistence/model
input. If sanitization cannot be verified, discard the capture and do not claim a verified
guide. Store tenant/task-bound artifact IDs, hashes and sanitization receipts; serve only to
same-tenant authorized employees via short-lived authorization. No public URLs or client
media transport is added. Reject screenshots from generated images/HTML or external URLs.

## Existing AI Teams integration (future implementation, not wired here)

1. Primary checks knowledge_context for its own allowed Knowledge Sources before call_expert.
   Reuse only a reviewed instruction compatible with operation, language and UI version.
   Current knowledge_context has no operation/version-aware guide contract: implement this
   narrow metadata lookup before enabling the pilot; do not treat a keyword match as proof.
2. call_expert selects the explicitly bound role=expert agent in this tenant. It keeps its
   own existing model, instructions and Knowledge Sources, no channel scopes and Manual mode.
3. Reserve using existing expert task/run/token ledgers; validate primary/expert binding,
   company caps, one task receipt and original channel authorization. No recursive delegation.
   Tool beinsystems_demo_browser needs a dedicated demo-only policy extension; do not add
   write capability to all existing read-only diagnostic tools.
4. Adapter receives server-created TaskScope, connection handle and canonical operation;
   never the entire raw customer conversation, customer credentials or free-form executable code.
5. Return an internal guide attachment to the expert result. Existing result() only permits
   cause/evidence/confidence/safe_actions/additional_information/requires_human/ticket_summary;
   extend this compatibly with an optional guide attachment, not arbitrary extra model fields.
   Keep result and screenshots behind employee permissions; model private reasoning is absent.
6. Existing _live/_settle and final primary send gates remain authoritative. Instructor has
   no WhatsApp/CRM/Resume imports or authority. Manual produces the normal primary draft;
   Automatic still requires its existing authorization. Human Mode/stale group_revision blocks
   primary delivery regardless of instructor success. This branch changes none of those paths.

Proposed structured guide attachment:
operation, title, language (ru/az/en), prerequisites[], steps[{number,instruction,screenshot_id}],
warnings[], ui_version, checked_at (UTC), verification.status (verified|not_verified),
verification.evidence_refs[], incomplete_reason, candidate_id (only after persistence).
Verification is assigned by trusted adapter evidence, never a model's claim. A verified
transfer requires the actual demo result/transfer identity and expected source/destination
quantity changes, observed in the same demo company; form navigation alone is not success.
An interrupted/incomplete guide has no publishable instruction status.

## Review, reuse and persistence

Use existing saas_ai_expert_tasks/result and saas_ai_agent_runs/token_usage for internal
receipts and billing. New guide metadata/screenshots require an explicit compatibility
extension of the existing result/candidate contracts after access is connected; no migration
or candidate creation is attempted here. Existing knowledge-candidates require extraction
receipts and real source message IDs: preserve the question's authorized message IDs, keep
artifact IDs as separate provenance, never insert artificial chat messages to satisfy FKs.
Store a guide as pending_review/needs_clarification, NOT an active Knowledge Source.
Employee review checks actual screenshots, verified steps and selected agent access; only
then use existing approve -> Knowledge Sources. Changes in UI invalidate verification.

Idempotency proposal: tenant + operation + language + UI build + synthetic fixture revision
+ instructor version, plus parent_run/expert receipt for the in-flight task. Never share
artifacts/knowledge across tenants or agents without existing source permissions. Reuse
reviewed material before opening a browser. An uncertain timed-out write is never retried
automatically. These are integration requirements, not implemented caches in this branch.

## Pilot acceptance, after safe connection

Demonstrate one warehouse transfer using synthetic data and actual screens, source/destination
checks and sanitized captures. Exercise forbidden company switching/settings/egress, rights,
PII capture failure, timeout/action/token limits, browser failure, Human/Pause transition,
stale group revision, Manual draft, parent return, employee review and approved-knowledge reuse.
Repeat once from an existing approved instruction and prove no new browser/model expense.
No client sends are needed for acceptance. Do not enable the tool until these checks pass.

## Local verification

34 offline contract tests passed. These check input shapes, tenant/task references,
forbidden actions/targets, fixture-only inputs, pause/revision/budget guards and artifact
receipt requirements. They do not test real navigation, capture sanitization, restore,
model generation or expert delivery. No browser execution was simulated as successful.
Only this specification, the unregistered contract module and its tests are added.
