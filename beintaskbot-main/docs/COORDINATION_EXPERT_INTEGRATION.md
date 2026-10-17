# Coordination + AI Expert integration

Integration branch: `codex/integration-coordination-expert`.
Verified remote main: `ffc5a677f01c0a2c1ecbf4f576036b5e5714567d`.
Inputs preserved by Git merge: Beintaskbot `da21e3aa`, AI Expert `6f498744` (including `fd8a0a72`).
Connect dependency: `8c830d2f32369ee6edc05c7c218e6eb12a8c6a17`, based on verified master `de67f9918a28586ef57002b398f135e1695864c8`.

## Integration changes

No textual merge conflicts. Both schema extensions, primary-only queue filters, group delivery fences, worker expert hooks and UI components are retained.

Expert admission, pre-generation and result settlement now validate the persisted group event receipt using `job_allowed`, in addition to current authority and `group_revision`. A fabricated/nonexistent event key cannot invoke an expert. This check is Connect-only; Kommo keeps its existing identity rules. Schema initialization commits before taking group/dialog locks. The old interface-only expert test now patches individual functions instead of replacing the coordinator module and accidentally removing its real lock implementation.

The primary remains the sole delivery authority. Expert results cannot directly send, assign, resume or alter group coordination. A blocked expert call may leave the authorized primary to answer without expert evidence; it does not authorize a different channel or another sender. Human/Pause or assignment revision changes during expert generation discard the result. Resume admits only new events. Manual produces a draft; Automatic uses the durable group send reservation and final authority check.

## Targeted validation

- 112 Python tests: new integrated group/expert worker scenarios (10), existing expert, group coordination, channel isolation, routes and Kommo regressions.
- 16 real PostgreSQL multiprocess tests: concurrent webhook admission, send reservations, worker/draft races, takeover and channel isolation.
- 33 Python tests plus 18 subtests: Test/Active gates and assignment permissions/legacy isolation.
- Total: **161 Python tests + 18 subtests passed**.
- Connect: **6 targeted tests passed**, including real PostgreSQL migration, durable concurrent reservations and early cross-channel AI echo.
- **4 JavaScript suites passed**: group coordination, expert editor, template library and support inbox.
- `git diff --check` passed. No tracked `/webapp` changes relative to main.

All data/provider calls were isolated fixtures or mocks. No real WhatsApp, paid model requests, production database migrations or deployment were performed. Existing agent configuration and sessions were not changed.

## Migrations and release boundaries

Beintaskbot uses existing schema initialization: additive coordinator/event/delivery/staff tables and expert task table; agent role defaults to primary, expert policy to empty, reserved token usage to zero. Tests exercise combined schema creation and existing personal/Kommo paths. No bulk reactivation, history deletion or legacy ambiguity resolution is introduced.

Connect migration creates the narrower tenant/conversation/provider-message uniqueness index before removing the old tenant-wide index; existing rows are retained (verified on PostgreSQL). Reverting that index after cross-channel receipts exist is unsafe without an ambiguity audit. Do not collapse or delete those receipts during rollback.

Future rollout order remains Connect first, then Beintaskbot and its workers. Before a separately authorized rollout, audit/quiesce old pending group outbox entries, which predate the new delivery fence; verify the real production schema and recovery plan. This integration does not certify a live migration or WhatsApp acceptance test.

## Remaining limitations

No code/test blockers found in the targeted integration. Deployment remains intentionally unperformed. Groups without explicit safe primary/channel assignment stay blocked; legacy quarantine remains. No secondary-channel fallback is added. An uncertain external send retains its permanent reservation rather than retrying. An already accepted provider request cannot be recalled.

Expert diagnostic adapters currently report integration_unavailable; actual expert capabilities are model generation and authorized knowledge retrieval. Expert per-conversation call cap is lifetime in stage one. No changes to live agents enable or configure these features automatically.
