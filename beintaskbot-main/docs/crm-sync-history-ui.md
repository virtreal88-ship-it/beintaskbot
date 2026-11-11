# Native CRM synchronization history UI

The contact, deal and company cards share `tenant-crm-sync-history.js`. The
panel reads only the local, permission-filtered event journal through
`GET /api/platform/crm/native/{contacts|deals|companies}/{id}/sync-history`.
It is mounted after the exact source card has been read successfully. An
explicit row-level read denial hides it, including for update-only users.
The endpoint remains the current exact source-read authority on every page.
There are no provider calls, retry/recovery writes, mapping changes or access
changes. Existing one-off local-save receipts are unchanged.

## Meaning and presentation

- RU/AZ/EN wording explains that each row is the latest saved state of one
  immutable event, not a full sequence of state transitions.
- A completion row means the journal recorded completion at its displayed
  timestamp. It does not claim that the external CRM currently matches.
- Pending, processing, unknown and unverified outcomes do not imply completion.
  Superseded means a newer local version replaced the event, not that no
  earlier delivery step ever ran.
- No saved events means no recorded events, not successful synchronization.
- Fixed event/state/outcome/issue labels, stable event IDs and formatted
  timestamps are rendered. Raw errors, payloads and provider metadata are not
  shown. Exact timestamp and revision ISO values, including microseconds, are
  retained in the time element's `datetime` and `title` attributes.
- Refresh and load-more only read the journal. Pagination preserves all three
  cursor fields (`before`, `before_id`, `before_kind`) without rounding its
  timestamp. Deduplication keys include both journal kind and event ID.

## Lifecycle and access

A history read captures the authenticated tenant/user generation and the
source-card opening. Requests send `expected_tenant_id` and
`expected_user_id`, use `no-store`, and are aborted/invalidated when obsolete.
Both the response and completion of JSON parsing must still match the live
card. Closing, replacing or hiding the card clears retained rows; authentication
revalidation, invalidation, profile authorization changes and navigation clear
the panel. A delayed source-card read cannot mount history under a new auth
generation. Errors, including 403/404, erase prior rows and do not render
server-provided error details. Saved external contacts have no native journal
panel.

## Verification

From `beintaskbot-main` with the existing jsdom dependencies available:

```sh
node tests/test_crm_sync_history_ui.cjs
node tests/test_crm_save_receipt_ui.cjs
node tests/test_native_company_deal_source_ui.cjs
node tests/test_native_company_relation_ui.cjs
node tests/test_contacts_directory_ui.cjs
node tests/test_tenant_crm_native_modal_ui.cjs
node tests/test_tenant_crm_native_workflow_ui.cjs
node tests/test_tenant_crm_sync_ui.cjs
python -m pytest -q tests/test_tenant_web_routes.py
```

The DOM suite executes the current app markup and controllers, while API
responses are synthetic. It verifies presentation, source-read wiring and
interrupted/repeated frontend flows. Database authorization and provider
isolation are covered separately by the backend history tests.

`tests/test_crm_sync_history_browser.cjs` adds real Chromium read-only
company/deal card checks at 1280px and 390px, viewport overflow checks and
screenshots, refresh deduplication, closing a pending read, 403 handling and
authentication clearing. All browser requests are restricted to synthetic
same-origin fixture assets. Run on browser-capable CI:

```sh
PLAYWRIGHT_MODULE=/path/to/node_modules/playwright \
CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
  node tests/test_crm_sync_history_browser.cjs
```

The browser runner was syntax-checked only in this container. Chromium had
already been denied by the environment at socket startup (`Operation not
permitted`) in the preceding source-card task; this work does not retry that
denied launch. No browser/visual pass or screenshots are claimed here.
