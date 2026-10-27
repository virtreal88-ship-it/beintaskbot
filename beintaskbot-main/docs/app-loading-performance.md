# App loading and dialog list reads

Baseline: `9bf09480f968e24e51aae090d4a854e26843ca97` (`origin/main` at the start of this change).

The app now shares concurrent profile and selected read requests within the current page. A successful profile response is reusable for the one-second bootstrap burst; ordinary read responses are not retained by the fetch coordinator. Dialog navigation may reuse the current list for five seconds only with the same tenant, user, role, active status, permissions, capabilities, filters and page. Explicit refresh, polling and read-mark completion re-fetch. There is no new localStorage/sessionStorage data cache.

Focus and visibility restoration conceal the dialog/agent views until profile authorization completes. Changed identity or permissions and authentication failure clear their retained data and invalidate old response bodies, including a delayed `json()` call. Restoring a page from the browser's back/forward cache reloads it. Dialog and agent GETs send optional expected tenant/user headers; the server rejects mismatches before reading. Clients without those headers keep the existing authorization checks. These headers cannot grant access.

AI configuration reads start when its view is visible. Independent settings, simulation settings, agent-list and cached option reads run together; overlapping load triggers share that operation. Loading, failed and successful-empty results have distinct states. Native CRM bootstrap reads run independently, retaining the options-before-render dependency where required.

The dialog backend evaluates one materialized authorized projection for rows, channel/agent choices and attention counts. Request-local bulk enrichment replaces per-row group, assignee and assignment reads. Existing candidate eligibility functions are reused; single-card reads and mutations retain their original authorization paths. Python decoration, effective-state filtering, ordering, global unread calculation and pagination remain in their original order. No schema migration, permission grant, agent activation, provider call or send was added.

## Controlled evidence

All measurements below use synthetic local data, mocked browser HTTP responses and PostgreSQL/WASM (PGlite), never production data or external AI/transport providers.

| Fixture | Baseline | Changed |
| --- | ---: | ---: |
| Twelve concurrent profile consumers | 12 network requests without sharing | 1 network request |
| Profile/install/view list triggers during one pending request | 3 independent calls without sharing | 1 list request |
| Existing media-preview startup fixture | 3 requests / 986 JSON bytes | 2 requests / 583 JSON bytes |
| Full projection executions per list request | 4 | 1 |
| One direct dialog, captured SQL statements | 36 | 24 |
| 121 direct dialogs, captured SQL statements | 1,356 | 24 |
| Three logical groups on two channels, plus one direct dialog | 64 | 26 |

The request-consumer rows are controlled trigger counts, not a measured production waterfall. The media fixture preserves the same history/media behavior while removing a duplicate initial list payload. SQL statement counts capture calls to the fixture's `execute`, including existing schema/read-cursor work, excluding transaction BEGIN/COMMIT. They are not timings or query-plan estimates.

`tests/fixtures/operator_list_baseline.py` freezes the original listing function from the baseline commit. The comparison uses the same unchanged projection, decoration, authorization and eligibility rules for both paths. It verifies complete normalized results for 121 routes, multiple pages, empty/out-of-range pages, search/channel/agent/state filters, cross-tenant exclusion, unread initialization/read marking, identity conflicts, folded groups, assignees and channel-specific group state. Existing operator/assignment/support suites cover further permission, job and mutation behavior. Timestamp strings are normalized to equivalent ISO instants before comparison.

The browser fixture exercises slow/failed/coalesced requests, delayed body consumption, header/credential isolation, superseded initial authorization, parallel bootstrap, hidden agents, empty/error states, filter/history response ordering, tenant/permission revocation, silent workspace changes on focus, delayed-auth notification deep links, read-mark/list races, polling, hidden-tab suspension, same-profile focus during initial and warm loads, and real chat-control action dispatch while agent configuration remains hidden. Configuration writes still require loaded settings, and revoked chat controls cannot send a request. Every network request is intercepted; synthetic mutations cover operator read marks and the existing chat Pause/Resume/Handoff actions only.

## Reproduce

From `beintaskbot-main`, with Python 3.11 and Node 22:

```sh
python -m pip install -r requirements.txt pytest
npm install --prefix .support-test-deps --no-save @electric-sql/pglite@0.5.11 jsdom@26.1.0
NODE_PATH=./.support-test-deps/node_modules node tests/test_app_loading_performance.cjs
python -m pytest -q -s tests/test_operator_list_performance.py
node tests/test_tenant_app_syntax.cjs
node tests/test_tenant_operator_auto_refresh_ui.cjs
node tests/test_tenant_dialog_avatar_queue.cjs
node tests/test_tenant_agent_library_ui.cjs
node tests/test_connect_chat_controls_runtime.cjs
node tests/test_connect_media_preview_ui.cjs
```

The focused SQL suite has 25 cases; the loading test reports eight browser/request scenario groups. `.github/workflows/app-loading-performance.yml` also runs the existing operator, assignment, support, copilot and web-route regressions. The frozen fixture needs no Git history in a shallow CI checkout.

## Limits

This is evidence of removed requests/round trips and preserved fixture behavior, not a production latency claim. No production EXPLAIN/ANALYZE or production load test was run. Materializing and JSON-aggregating the visible projection trades repeated computation for server memory/serialization; the list still evaluates all authorized routes before effective-state filters and global unread totals. These costs and performance on very large tenants remain to be measured separately. The baseline oracle shares unchanged helpers and is not a wholly independent implementation; existing integration tests and final review remain necessary.

## Case-link follow-up and bounded server timing

An explicit case/review link takes precedence over the session's saved dialog. The resolver no longer races a saved-route history request. Late resolver results are discarded after a different selection, Close/Escape, navigation away, Back/Forward, or authorization/scope change. An interrupted authorization check restarts a pending URL link only after the fresh profile arrives. A failed/invalid explicit link does not fall back to an unrelated saved conversation; ordinary navigation still restores it.

The controlled saved-route + delayed-case-resolver fixture produced two history requests before this follow-up and one anchored history afterwards. The Chromium smoke covers six language/viewport combinations, each on cold load and reload with a different saved conversation, and asserts exactly one history each time. This is a request-count result, not a production latency measurement.

`tenant_read_timing.py` measures GET dialog-list, history, and case-conversation resolver handlers. A fixed `operator_read_timing` log contains only numeric fields: resource code (1=list, 2=history, 3=resolver), completed (work returned successfully), auth_ms, queue_ms, work_ms, resume_ms, and total_ms. Missing stages use -1. There are no paths, query strings, cookies, tenant/user/message IDs, request bodies, or exception text. Logging is capped at one completed handler per resource every 60 seconds per process, including failures; logging failure cannot affect the response. This first-eligible sample is **not** a representative percentile or a per-user trace.

`auth_ms` times the existing synchronous membership/session check without changing or caching it. `queue_ms` is executor submission to worker start; `work_ms` covers the complete listing/history/resolver function, not SQL alone; `resume_ms` measures worker completion to coroutine resumption. `total_ms` covers the handler through response construction, excluding proxy queuing and network delivery. Blocking authentication elsewhere may affect these stages. The measurements support locating remaining delay before any auth-offloading or server architecture change. Existing focus checks and every-request revocation/permission checks remain in place.

Run `python -m pytest -q tests/test_read_timing.py` for deterministic stage boundaries, rate limits, exception handling, numeric-only logging, and authorization on repeated requests. The loading fixture now reports nine scenario groups.
