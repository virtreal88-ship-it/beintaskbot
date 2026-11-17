# Deal stage / Info release

Scope: remove repeated customer/name, phone, stage and Kommo ID from Info, retaining header and duplicate lookup. Reconcile an explicit uncertain stage receipt once by GET under the existing UUID; never repeat PATCH or retry rejected preflight.

Observed runtime logs: two stage POSTs on November 16 (19:12:40 and 20:49:06 UTC) returned 409. Around the first, a deal editor GET and board preferences GET preceded the POST; Kommo update/status webhooks arrived before the 409. This is consistent with delayed confirmation but not definitive proof: logs do not include response error code and cannot associate webhook payloads safely. Do not claim the original production symptom conclusively diagnosed.

Reproduction: actual modal script plus actual command helper with a synthetic POST outcome_unknown followed by confirmed GET. Before change the header remains stale; after change it adopts confirmed stage while retaining draft. Tests also cover unresolved receipt retaining original command, subsequent GET-only retry, and denied preflight causing no verification/write retry.

Local: focused JS suites and 24 Python tests pass; bundle rebuilt deterministically. Chromium cannot start locally because sandbox socket creation is denied, so browser tests are required in CI. No live deals changed and no customer messages sent.

Compact-card/mobile-drag changes, bulk completion and rejected-call changes intentionally excluded from this release.
