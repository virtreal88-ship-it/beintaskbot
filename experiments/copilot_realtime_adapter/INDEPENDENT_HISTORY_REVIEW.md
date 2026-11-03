# Independent history compatibility checks

These test-only checks are based on Draft #480 head
`4be1c5c0bb3154814708dcdc556f6daec4f33cf2`, compared with remote main
`fc75c2899b7b86de42bf2cafdbdd116e9717d821` on 2026-11-03. They make no
production changes or paid calls. They do not establish rollout safety.

## Additional scenarios

* `test_history_preexpand_review.py`: against a disposable PostgreSQL fixture
  with an existing pre-expansion thread table, call the actual chat schema
  bootstrap with only the unrelated global bootstrap patched out. The bootstrap
  leaves `clear_revision` absent; the actual new history reader then raises
  `UndefinedColumn`. The review-only additive SQL makes the same read succeed.
  This confirms that expansion must precede deploying the new reader; it does
  not authorize running the expansion or prove its production lock impact.
* `test_history_receipt_replay_review.py`: using the actual Copilot run and a
  synthetic model, complete a request, clear the thread, then retry with the
  same actor and request ID. The prior completed response is replayed without
  another model call while the thread remains empty. The receipt still contains
  the text. The base main also deletes only the thread on clear and replays a
  completed receipt; this is existing semantics, not a new regression. Do not
  describe clear as deleting every copy of the answer. Separate 90-day bounded
  receipt-result cleanup is invoked by a different path and is not proven to
  run on a fixed schedule.

For an existing schema this evidence is **no-go without a verified expansion
and safe mixed-worker transition**. Old text finalizers can overwrite current
thread content, and old scoped DELETE of an absent row leaves no clear fence.
The existing `ROLLING_HISTORY.md` and PostgreSQL tests document and demonstrate
these separate mixed-version blockers. An old cached UI also ignores the new
`history_status=cleared` warning, though it still displays the answer; the
updated UI displays the warning. Both local JSDOM tests confirm no automatic
customer send. No runtime/fleet drain, browser rollout, lock impact, rollback
after voice writes, source fence or production behavior is attested.

## Local reproduction

Set `VOICE_AUTH_PG_URL` to a **disposable localhost** database named
`test_voice_auth_` plus 32 lowercase hex digits. The inherited fixture validates
the URL and creates/removes a random schema. From repository root, with test
dependencies available:

```sh
PYTHONPATH=beintaskbot-main:experiments/copilot_realtime_adapter \
 python -m unittest -v test_history_preexpand_review.Review.test_existing_schema_bootstrap_does_not_expand_before_new_read

PYTHONPATH=beintaskbot-main:beintaskbot-main/tests \
 python -m pytest -q beintaskbot-main/tests/test_history_receipt_replay_review.py
```

The second check requires the project's local PGlite test dependency and uses a
synthetic key and mocked provider. JSDOM tests require the local test-only
`.support-test-deps` setup; set `TEST_LEGACY_COPILOT_UI` to a local copy of the
main-commit `docs/tenant-operator-dialogs.js` to verify the old-client case.
No real keys, contacts or provider calls are needed.
