# Integration release plan: PR #100–#106

## Scope and baseline

- GitHub integration base: `origin/main` at `da6b926` (includes merged PR #100 and #101).
- Integration adds PR #102–#106 in order. The last confirmed successful Railway worker deployment is PR #99, commit `8f2d7da` (`cdc0aece-1f71-483f-9924-10b617b16acd`). The attempted PR #100 deployment failed; production deployment of #101–#106 is not confirmed.
- This document is a release checklist only. No main merge, Railway deployment, rollback, production data change, AI call, or WhatsApp send was performed.

## Release gates

1. Review the integration PR and confirm the source branch still contains the intended versions of all five PRs. Recheck GitHub `main` before final merge; do not force-push.
2. On an isolated PostgreSQL instance matching the production major version, restore a disposable schema snapshot and run the complete schema initialization twice. Verify existing Telegram users, memberships, invitations, CRM data, agent state, conversations, media metadata, candidate cases, and publication records remain intact. Run login/session revocation, RBAC, tenant-isolation, and rollback compatibility checks. The committed PGlite migration regression is useful coverage, but is not a substitute for this PostgreSQL gate.
3. Run the full Python and UI suites in the project's supported isolated test setup. Triage all failures against the same `origin/main` baseline; resolve any new failures before release. Do not use tests that send real CRM/WhatsApp traffic or call paid AI providers.
4. Run authenticated Chromium checks with synthetic data at 1440×900, 1920×1080, and a mobile viewport; cover RU/AZ/EN, both themes, dialog refresh, support-case review/publication safeguards, Connect and integrations navigation, and employee access screens. Confirm polling adds no AI/Evolution requests and does not reset drafts, filters, selected dialog, or scroll position.
5. Only after these gates pass, schedule deployment with the owner. Keep AI agents, Connect sessions, automation modes, pilot controls, and customer data unchanged during rollout.

## Rollback to the confirmed PR #99 deployment

1. Before release, take/verify a Railway PostgreSQL backup and record the current worker deployment ID and integration commit. Do not roll back until the owner authorizes the release action.
2. If the release fails its smoke checks, use Railway's rollback for worker service `74f19d4a-e085-486d-b1c8-2100d8c30d78` in project `powerful-solace` to the confirmed PR #99 deployment `cdc0aece-1f71-483f-9924-10b617b16acd` (`8f2d7da`). Do not redeploy the failed PR #100 deployment.
3. PR #104's schema changes are additive (new tables/columns and replacement role constraints). Keep them in PostgreSQL when rolling back application code; do not run destructive down migrations or restore an old database snapshot over newer customer writes. If schema changes are found to be incompatible, stop and use a reviewed forward-fix.
4. After rollback, verify worker health, login and tenant access, dialog list/history, Connect state, and support-case reads. Confirm no pending release deployment remains and that customer messages or background AI work were not replayed.

## Current verification status

- Chromium review found that the integrations catalog was translated while its outer header and CRM sync status remained Azerbaijani after switching languages. The integration branch now localizes those labels and status messages; `tests/test_integrations_localization_ui.cjs` checks live RU/AZ/EN switching.
- PR #102 auto-refresh UI test passes. PR #103/#105's nine known knowledge-case expectation/evidence failures also reproduce on unmodified `origin/main`; they are pre-existing and still need explicit triage before release.
- PR #104 unit/API tests pass in the targeted run. The integration branch adds a PGlite regression that applies the identity migration twice and checks legacy account preservation. No local PostgreSQL server or disposable PostgreSQL DSN was available, so real PostgreSQL migration rehearsal remains a release blocker.
- Chromium synthetic preview passed basic support-case rendering, filter expansion, locale and theme interaction at the available 1280×720 viewport. Required 1440×900, 1920×1080, mobile, authenticated flows, and production-shaped API checks remain outstanding.
- The broad pytest run is not release evidence: 2,243 passed, 56 failed, 21 errored, and 38 skipped; the existing suite mixes module-level Psycopg stubs, embedded PGlite, and optional real-PostgreSQL tests in one collection process. Failures outside the known baseline set must be rerun in the supported isolated setup and triaged before release.
