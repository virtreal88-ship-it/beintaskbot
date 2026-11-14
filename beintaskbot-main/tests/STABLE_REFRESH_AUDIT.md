# Stable background refresh audit (2026-11-14)

## Verified causes and changes

| Trigger → request → state/DOM | Finding | Change |
| --- | --- | --- |
| Task refresh/sync → source GET → `phase=loading` → empty React rows | Loaded task cards were removed and recreated; no document navigation | Preserve ready/stale snapshots during background reads. First read still has loading UI; errors retain the authorized snapshot and show status. |
| AI request + same-dialog history refresh → incremented open revision → deferred render | Pending AI could lose its completion callback; full `innerHTML` replacement detached editor/media | Separate read and mount generations. Patch message evidence/content in the mounted same-route subtree, preserving handler page references. Metadata affecting permissions or routing still invalidates the mounted UI. |
| Completed Kommo job read again → repeated complete event | Previously completed jobs could trigger unnecessary refreshes on subsequent polls/profile checks | Track completed job timestamps per identity, resetting a resource when a new run is seen. Polling cadence unchanged. |
| Sync completion → two task listeners | Redundant subscriber confirmed; existing task single-flight already bounded the measured request count | Skip the legacy listener when task-flow owns refresh. This is **not** a measured 3→2 request improvement. |
| Deal GET completes → render all deals | Search was ignored on refresh; native rows used wholesale replacement | Reapply search, retain keyed native rows; discard superseded/old-scope responses and coalesce identical reads. |
| Same profile reauthorization → deal preference `load()` → null data/closed column editor | Column modal/draft reset without permission change | Reload on changed scope; unchanged authorization preserves column editor. Logout clears preferences and pending generations. |
| Contacts refresh → loading placeholder/error → list replacement | Existing contact rows disappeared on loading and network errors | Retain same-query authorized snapshot and unchanged row nodes. Query/source changes and access denial still clear the old snapshot. |

React roots in `ui/src/main.jsx` are already cached by host; root remounting was not the task-list cause. Existing workspace integration placement moves existing nodes. No wholesale workspace rewrite. Task minute timers now stop while hidden/off-page and unsubscribe on unmount. CRM polling is stopped on pagehide and resumes on BFCache restore.

Main was reconciled with `c90c4aa0`, including PR #665 (deal source selection/compact sync status) and #656 (task visibility). The new source selection is preserved. Scoped deal clearing uses the existing renderer; the parallel branch referenced a nonexistent optional `clear()` method.

Release reconciliation includes main `6e7f3e48` (PR #667/#668/#669): Profile settings, draggable task/deal boards and the common deal toolbar remain intact. Native and Kommo board authorization listeners now distinguish repeated same-profile checks from changed permissions/identity. New board cards remain mounted during same-profile checks and single-record refresh; invalid authorization still clears them. The built-browser suite exercises both boards and the relocated column editor.

## Browser evidence

Synthetic HTTP boundaries use shipped application controllers and the **built** React bundle. No provider generation, client messages or production writes occurred. Owner and limited-role fixtures: 390px and 1440px. Before assets are read from `13b9a082`, not the working tree; after assets are the branch build.

- Tasks: during a delayed refresh, tracked card was detached before / remains connected after; same data removed/recreated it before / retains the same node after. This measures DOM identity, not an instrumented React render count.
- One task sync completion: **2 source GETs before and after**. A burst of 6 refresh calls: **2 source GETs** (native and Kommo). No request frequency increase.
- AI: before, pending busy state remained stuck in the refresh race. After, one explicit click produces one generation, editor and audio element survive, and voice disposal is not invoked. Error and late completion after switching dialogs are covered.
- Deals: five concurrent completion notifications produce one GET; search and column editor survive unchanged authorization; a changed native record preserves its row; network failure retains data; company switch rejects delayed old data.
- Task error, denied source, logout and out-of-order reads are covered. Existing task suite also covers workflow-only revocation and company collision. Contact suite covers query/body races, scope changes, and retained snapshots vs denial.
- 40 navigation cycles: DOM count remains 1220 on the baseline and 1228 after the parallel deal-source UI merge (constant within each run). GC heap before/20/40 cycles is about 9.37–9.44 / 9.86–9.90 / 9.99–10.03 MB on baseline and 9.60–9.67 / 9.96–10.00 / 10.05–10.10 MB after. The second 20 cycles add approximately 0.10 MB after; synthetic request recording and warm-up are included. This short run does **not** prove absence of a long-running memory leak.

Playwright traces, refresh screenshots and raw metrics: `tests/artifacts/stable-refresh/{before,after}/`. CI uploads after traces as `stable-background-refresh-traces`. Artifacts contain synthetic data only and are not committed.

After adding the parallel native React board, the release-integration run has 1241 DOM nodes initially and 1264 after the first board visit, staying at 1264 after 20 and 40 transitions. Heap is approximately 9.79–9.83 / 10.31–10.34 / 10.44–10.47 MB. This reflects lazy board mounting, with about 0.13 MB added during the second 20 cycles; the earlier measurements above belong to the pre-board build.

## Checks

- `npm ci --prefix ui` and `npm run build --prefix ui` (Vite succeeds; existing bundle-size advisory).
- New `test_stable_background_refresh_browser.cjs` and `test_stable_deals_refresh_browser.cjs`.
- `test_task_read_save_flow_ui.cjs`: 25 groups.
- `test_contacts_directory_ui.cjs`, including new delayed/error/denial regression.
- `test_dialog_audio_transcription_ui.cjs`, `test_tenant_operator_copilot_ui.cjs`, `test_tenant_operator_mobile_ui.cjs`, `test_tenant_operator_auto_refresh_ui.cjs`.
- `test_operator_case_recovery_ui.cjs` checks incoming case-draft conflicts without replacing the existing employee draft.
- `test_dialog_avatar_card_browser.cjs` verifies saved customer metadata still updates on mobile/desktop. The rendered route snapshot is independent of the mutable history model, preventing a stale header after a background read.
- Media regressions verify replacement bytes update only their message, a complete history removes absent media before late bytes arrive, and a partial history preserves older loaded messages. Existing visible-photo/audio suites verify loading, playback, cooldowns and stale-source rejection with actual synthetic media bytes. Attachment-menu locale invalidation now changes the real locale instead of assuming a redundant event replaces DOM.
- `test_deal_source_picker_ui.cjs` and `test_deal_source_picker_browser.cjs` preserve the parallel native/Kommo source selector, filtered counts and compact sync status on light/dark 390/1440 layouts.
- `test_hidden_panel_loading.cjs` (including initial authorization with no hidden deal preference read), `test_tenant_crm_sync_ui.cjs`, `test_app_loading_performance.cjs`, `test_helper_request_identity_browser.cjs`, `test_kommo_board_ui.cjs`, `test_tenant_workspace.cjs`, `test_tenant_app_syntax.cjs`.
- Added a built-browser CI job; source tests alone are not acceptance evidence.

## Release and limits

This document describes local branch validation, not a production release. Production deployment, real owner/employee accounts and a live media/voice conversation have not been verified by these synthetic tests. They must be reported separately after deployment. The actual provider/lease implementation, Kommo business rules, assignments and message routing are unchanged.
