# ChatGPT → GitHub Issue → Codex handoff

GitHub Issues are the durable queue for evidence-based engineering work. The
workflow deliberately does not treat opening an issue as a command to run an
agent. A task must have a machine-readable contract and the `codex:ready`
label. The optional Codex job additionally needs `codex:approved`, a protected
GitHub Environment named `codex-readonly-approved`, a repository
`OPENAI_API_KEY` Actions secret, and the repository Actions variable
`CODEX_HANDOFF_EXECUTION_ENABLED` set to exactly `true`.

The repository cannot configure that OpenAI credential itself. An owner adds it
in GitHub **Settings → Secrets and variables → Actions**, then protects the
`codex-readonly-approved` environment with the owner as a required reviewer.
The execution variable is an explicit spend gate: leave it unset, or set it to
anything other than `true`, to validate handoffs without invoking a model.
This is intentionally a one-time repository setup, not a Beintaskbot or
customer credential. Until all controls exist, the workflow validates and
reports a task but cannot start Codex.

## Contract

Put this marker in the issue body. IDs are identifiers only; do not include
customer message text, media URLs, secrets, access tokens, passwords, or SQL.

```html
<!-- codex-handoff
{"schema_version":1,"mode":"read_only","evidence":["MCP run ID shows queued state"],"tenant_safe_ids":{"tenant_id":"00000000-0000-0000-0000-000000000000","queue_id":"00000000-0000-0000-0000-000000000000"},"severity":"P1","acceptance_criteria":["Explain the queue admission result without invoking a model"],"approval_state":"not_required_read_only","dedupe_key":"simulation-queue:00000000-0000-0000-0000-000000000000"}
-->
```

`mode` is one of `read_only`, `dry_run`, or `change`. The workflow rejects
automatic execution of `change` work even if the contract says
`owner_approved`: a GitHub label is not a Beintaskbot server-side approval.
Such work must use the application's future approval endpoint, with a bound
action, canonical parameters, object version, expiry, single use, audit trail,
and rollback record.

## Lifecycle

1. ChatGPT reviews allowed Beintaskbot MCP data and creates or updates the
   issue contract. It does not place raw customer content in GitHub.
2. `codex:ready` invokes validation and posts a status comment. It cannot
   execute application writes.
3. The owner may apply `codex:approved` to a **read-only/dry-run** task. The
   protected environment and execution variable are independent GitHub gates.
   Codex gets no Railway, production database, WhatsApp, or OpenAI application
   secrets.
4. Codex posts its outcome, test result, PR link, and rollback note to the
   same issue. It never merges or deploys from this workflow.
5. ChatGPT reviews the issue and the tenant-scoped MCP evidence. A sensitive
   production change stays pending until the application records a separate
   server-side owner approval.

The workflow uses the supported `openai/codex-action` pattern, but no issue is
assumed to launch Codex merely because it was created. The task contract,
label, Actions secret, and protected environment are all required.

## Issue #152 pilot

The queue diagnostic is available as `simulation_queue_diagnostic_read` through
the existing owner-authorized MCP connection. It accepts only a queue UUID and
returns no message content. For queue item `2f10a768-b169-45be-9e81-bcc15f0ff93c`,
it distinguishes a global feature flag, tenant configuration, company mode,
route/channel availability, start/end time, concurrency, and message-limit
admission from later processing failures. It performs no claim, replay, model
call, or write.
