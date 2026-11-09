# Explicit operator learning retry

The operator submission response may advertise `preflight_retry_available`.
This is only a read hint. A separate user click submits `retry_request_id` with
the unchanged bounded selection. Ordinary submission still deduplicates without
starting another extraction, including after a failed response.

Admission rechecks the current tenant, support/dialog permissions, selected
source references, agent version/assignment and dialog binding under the
existing `tenant-chat-ai:<tenant>` transaction lock. It requires:

- A completed failed extraction and a skipped AI run, both with the exact
  `extraction_preflight_failed` code. `finish()` records skipped only when its
  aggregate attempted flag is false, including ASR and visual attempts.
- A matching token row with zero input/output/total/reserved tokens, no
  generated timestamp, and no candidates.
- An operator training target and explicit false external-media, ASR and vision
  permissions. Campaign, grant, review and special media requests are excluded.
- Exactly one matching spend reservation at `extraction:<parent UUID>`, released
  with actual cost zero and a completion timestamp. No media reservations.

The spend release is a separate transaction from extraction completion. Until
that release commits, retry remains blocked. Missing historical spend receipts
are also blocked: current configuration is not evidence that an old request ran
uncapped. Null token usage alone is never proof of no provider dispatch.

The successor keeps its predecessor in `route.retry_of`. Its creation and the
existing-successor lookup share the tenant lock, so concurrent clicks and lost
responses return the same successor. There is no automatic retry, source-wide
replay, change of permissions, or retry of an unknown/provider-attempted run.

Tests use mocked provider calls, isolated PGlite, and a genuine two-process
localhost PostgreSQL race. No production request is replayed by the tests.
