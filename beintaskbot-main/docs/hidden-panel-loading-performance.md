# Hidden News / Settings loading

Baseline: `50cc25d5e38a3e80c253c5d4960d5da5f41550be` (main after PR303).

## Measured request baseline

`tests/test_hidden_panel_loading.cjs` executes the shipped React bundle and the
actual News/settings scripts in JSDOM. All HTTP responses are synthetic; the
fixture cannot reach an account, provider, model, or production data.

With an authorized owner on Dialogs, after the shared `/api/platform/me`:

| Hidden consumer | Baseline GETs | After GETs |
| --- | ---: | ---: |
| News list + Telegram availability | 2 | 0 |
| Legacy News AI / Telegram / Chat AI settings | 3 | 0 |
| React personal settings (sessions + notification preferences) | 2 | 0 |
| React integrations status (Linear / Chat AI / Telegram / BeinSystems / Connect) | 5 | 0 |
| **Total** | **12** | **0** |

This is an executed request-count comparison, not a production latency claim.
It confirms the earlier static estimate of 12 for this owner fixture. Roles and
enabled modules can produce different counts.

Reproduce from `beintaskbot-main` after installing UI dependencies and
`jsdom@26.1.0` in `.support-test-deps`:

```sh
NODE_PATH=./.support-test-deps/node_modules node tests/test_hidden_panel_loading.cjs --baseline-ref 50cc25d5e38a3e80c253c5d4960d5da5f41550be
npm run build --prefix ui
NODE_PATH=./.support-test-deps/node_modules node tests/test_hidden_panel_loading.cjs
```

## Dependencies and remaining waits

- News navigation uses the existing `/me` member, Linear module and per-user
  `can_review_news` binding. Owner and bound-worker visibility matches the
  existing server policy; disconnecting Linear does not remove review access.
  API authorization remains server-owned and unchanged.
- A deliberately delayed `/me` fixture confirms that navigation waits for this
  one shared authorization request. After it resolves, News needs **zero**
  secondary reads to become available. `/me` server/SQL latency remains outside
  this change; no production milliseconds were measured.
- The first News visit still performs its list GET followed by Telegram
  availability GET. The existing reader waits for both before displaying rows.
  Integration status still performs five parallel GETs after `/me`; personal
  settings perform two parallel GETs. These are remaining first-visit waits,
  now paid only when the relevant surface opens.
- Legacy Linear catalog loading remains explicit. OpenAI and Telegram settings
  load when their integration modal's Settings tab requests the legacy panel.
  Personal sessions/preferences load on the visible Diagnostics section, not
  just because the Settings page exists.
- Push device initialization, dialog polling, chat controls, and the agent
  dashboard are not registered with the new demand gate. Existing dialog
  request reuse, read-marker handling, and real-time refresh are unchanged.

## Retention and invalidation

The gate retains rendered state only in the current page, with a 30-second
reuse window on return. It does not cache HTTP responses or persist API data.
Explicit Refresh and existing save handlers keep their current reads/writes.
A changed tenant/user, role or authorization clears retained UI. Display metadata
and integration freshness refresh clean panels without discarding edited forms.
Logout, workspace switch, pagehide and failed reauthorization reset it. Pending
reauthorization conceals and blocks the affected roots and portals; the same
authorized identity resumes edited forms and reloads clean panels.
Late legacy responses are rejected by their existing incremented generation.
React roots are unmounted before remounting for a new context.

Edited forms also survive an ordinary return after 30 seconds. Legacy successful
load/save clears their dirty marker after the existing response-generation guard.
Marketplace search/category do not mark settings dirty. React account/connection
forms conservatively retain edits until an authority reset or page reload; this
can retain their displayed snapshot longer than the clean-panel window.

No new real-time transport is introduced for News or settings. Their local
save/refresh paths remain live, profile events invalidate changed state, and
returning after 30 seconds reloads. Dialog real-time updates remain independent.

## Verification

- New executable regression suite: delayed auth, 12-to-0 comparison, direct
  News/Settings/Integrations links, real history back/forward, warm/expired
  visits, explicit Refresh, owner and Linear-bound worker access, revocation,
  company/user switch, logout, delayed responses and same-scope focus. Follow-up
  coverage verifies dirty forms across background profile refresh, pending auth,
  clean sibling replacement in a retained portal, failed auth and expired return.
- Real React marketplace Settings/Close/reopen flow plus legacy placement
  cancellation. Detached hosts retain their panels; explicitly auth-reset
  panels cannot be resurrected by cleanup.
- Existing app-loading, dialog refresh/avatar, News/Telegram/Linear, integration
  localization and Chat AI UI contracts pass. Photo and Copilot fixtures still
  exercise their unchanged settings logic after explicitly opening the panel.
- 49 Python tests pass for asset routes, app design, News permissions and APIs.
  One existing whitespace-sensitive design assertion already failed on the
  baseline JSX formatting; it now ignores whitespace while checking the same
  Settings callback.
- UI bundle rebuilt. Vite's existing large-chunk warning remains; this patch
  defers data loading and does not split the JavaScript bundle.

No merge, deployment, agent activation, provider calls, client delivery, model
configuration, permission changes, or production writes were performed.

## Follow-up independent review

The independent read-only review identified and the follow-up fixes cover:
background /me metadata destroying drafts; auth-pending destroying same-user
drafts; TTL destroying edits; a clean sibling panel disappearing from a retained
React modal; and a marketplace search disabling status freshness. News Cancel
now clears its draft marker, so it does not suppress later list refreshes.

Known inherited limitation outside the demand subscriptions: the inline legacy
Connect configuration panel is not context-reset by this gate. A silent same-page
change to another tenant can retain its old fields in the repository; normal
workspace switching reloads /app. No new regression or production exposure was
established, but that legacy panel and its inline async handlers need a separate
scope-lifecycle review before claiming isolation for every Settings surface.
