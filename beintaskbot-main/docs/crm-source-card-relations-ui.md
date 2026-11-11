# Company/deal source-card relation UI

The server supplies the mode explicitly. Deal records and deal options expose
`scoped_relations` plus `relation_actions.link_company` and
`relation_actions.unlink_company`. Company records expose
`relation_actions.link_deal` and `relation_actions.unlink_deal`.

- In scoped mode, changing a company from a deal card or deal form uses
  `POST /api/platform/crm/native/deals/{id}/company-link` with exactly
  `{company_id, unlink}`. Controls use the source deal's update authority.
- From a company card, the existing company endpoint receives
  `{action: "link_deal" | "unlink_deal", entity_id}`. Its row-specific source
  company authority governs controls, without target deal update/delete.
- Gate-off deal operations retain the original company endpoint and legacy
  permission guards. There is no fallback from a failed scoped write.
- Update authority never requests an otherwise denied target directory. A
  denied related list does not destroy the source card; its already known
  company can still be detached when source update is allowed. Editing a deal
  preserves its embedded existing contact even when the contact directory is
  unavailable, instead of accidentally clearing the contact selector.
- Initial deal contact selection comes only from the existing server-filtered
  deal options. Deal creation does not require deal update. A create-only save
  does not subsequently request the deal directory. A separate post-create
  company change still requires deal update.
- A `scoped_relation_sync_not_configured` response leaves the link unsaved,
  shows a relation-specific error and does not retry another route. Pending
  relation clicks send once. A closed card or a newer opening cannot be
  overwritten or reopened by an older relation completion.

## Local verification (2026-11-11)

Passed against the current application markup and controllers:

```sh
node tests/test_native_company_deal_source_ui.cjs
node tests/test_native_company_relation_ui.cjs
node tests/test_tenant_crm_native_modal_ui.cjs
node tests/test_contacts_directory_ui.cjs
node tests/test_tenant_crm_native_workflow_ui.cjs
node tests/test_crm_save_receipt_ui.cjs
node tests/test_tenant_crm_sync_ui.cjs
node --check tests/native_company_deal_fixture.cjs
node --check tests/test_native_company_deal_source_browser.cjs
git diff --check
```

The new DOM suite loads the real `/app` inline controller and its supporting
assets. API responses are synthetic, so this validates frontend request shape,
visibility, denial handling and interaction lifecycle, not database
permissions. The separate backend PostgreSQL/HTTP suites establish those
boundaries.

## Chromium runner and environment limitation

The checked-in runner covers 1280px and 390px layouts, company-source and
deal-source linking/unlinking, exact request bodies, readable-contact creation,
409 handling, repeated clicks, closing during a pending write, and known-target
unlink with a denied company directory. Its HTTP fixture serves current app
assets and blocks external browser traffic. It performs no provider calls or
production writes.

Dependencies: Node, `jsdom` in `.support-test-deps/node_modules` for DOM tests,
`playwright` for the browser runner, and an installed Chromium executable.
Normal CI command from `beintaskbot-main`:

```sh
CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
  node tests/test_native_company_deal_source_browser.cjs
```

By default the runner loads Playwright from
`.support-test-deps/node_modules/playwright`. If CI installs it elsewhere, set
`PLAYWRIGHT_MODULE` to that absolute package path, for example:

```sh
PLAYWRIGHT_MODULE=/tmp/crm-source-browser-deps/node_modules/playwright \
CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
  node tests/test_native_company_deal_source_browser.cjs
```

**Browser assertions did not run in the current container.** Both the normal
launch and one permitted escalated launch failed before page creation with
Chromium `process_singleton_posix.cc` reporting `socket() failed: Operation not
permitted` and SIGABRT. There are no browser screenshots or visual-pass claims.
The runner is syntax-checked but still needs execution on browser-capable CI.
The temporary Playwright installation did not modify repository manifests or
the shared DOM test dependency directory.
