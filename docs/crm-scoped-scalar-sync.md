# Scoped scalar synchronization

New human contact/deal mutations with an effective scoped profile now write a
separate scalar journal in the same transaction as the native row mutation. The
API does not enqueue a second actorless legacy event. A public `sync` receipt
separates `local_saved` from external `pending`, `needs_verification`, `off` and
`unchanged` states. No response claims external synchronization completed merely
because the local card was saved.

An event contains source actor/action, tenant/card, exact local revision, immutable
changed-and-allowed scalar field mask, before/after values, content fingerprint,
and verified account/generation/record mapping. Relations never enter its mask.
Policy additions cannot expand an accepted event. Dispatch locks current source U,
tenant/member/department, native record, integration, mappings and provider policy
until the single PATCH/read-back completes. Later local revisions are rejected;
no worker exports another actor's newer row values under an older event.

The Kommo transport uses a single object PATCH for a mapped existing lead/contact,
with only name or supported custom scalar fields. It does not call the legacy
record exporter, POST/create, link/unlink, refresh tokens under locks, or replay an
uncertain request. Known divergent remote field values conflict. Existing single
phone/email category metadata is retained; multiple provider values are not
flattened. Timeout, crash or unavailable read-back is unknown, never an automatic
retry. A prior unknown/sending event blocks later writes for the same card.

## Legacy behavior

Records that have never had scoped provenance retain their original legacy queue
and payload behavior, including gate-off operation. There is no blanket legacy
queue pause and no global field stripping. A legacy event for a record with a scoped provenance head is rejected as
`legacy_scoped_revision_conflict`, whether its revision is older, equal or newer.
A later service/legacy phone edit cannot authorize exporting a prior scoped name
through a full-record payload. This is record-specific quarantine, not successful
sync or inferred service/human authority. Unrelated legacy/service cards are
untouched. The per-record barrier persists when the rollout flag is disabled:
rollback cannot erase a scoped value's provenance or authorize its export through
an actorless event. No markers are deleted or reset. Writer provenance for
subsequent mixed-writer events and an explicit resolution path for quarantined events remain activation blockers.
Already in-flight actorless requests cannot be recalled. Historical provenance
and a drain/transition plan remain prerequisites for activating scoped mode.

## Explicit current limits, not successful synchronization

- Legacy external IDs are never silently adopted as account-bound mappings.
  Missing verified record mapping produces `needs_verification` after local save.
- New scoped creates do not silently create a provider record under update
  authority. They report `needs_verification` with their original create action.
- BEIN account-bound scalar identity, pipeline/stage catalog bindings, and an
  independently verified phone/email clearing contract are not implemented by
  this change. These specific events report `needs_verification`; mapped Kommo
  name/title/nonempty single phone/email edits can use the guarded scalar path.
- Expiring OAuth requires a refresh outside the dispatch fence. The bound
  transport neither refreshes nor retries a 401 while authorization locks are held.
- A newer edit explicitly supersedes an older unsent event. Its original values
  and unsent outcome remain in the journal. The new receipt includes
  `review_required` and `superseded_unsent_event_ids`. The new event contains only
  its own changed fields and never silently coalesces another actor's unsent values.
- Contact/deal save paths now display the receipt in RU/AZ/EN, distinguish local
  save from queued/unverified/conflicting external synchronization, and explicitly
  warn about an earlier unsent value. A separate notice survives list refresh and
  remains visible inside a partially completed form. It makes no provider reads,
  retries or mapping changes. Auth revalidation clears old-context receipts.
- Native contact/deal/company cards now have a persisted, read-only event history,
  authorized by current R of the exact source card. It shows the latest stored
  outcome for each event, with immutable event identity/local revision and safe
  issue labels. It does not display payloads, actors or provider/account metadata.
  A recorded completion is not a fresh check of the external CRM. Empty history
  does not establish successful sync or mean that no older external work exists.
- There is still no recovery or mapping-verification UI. History cannot retry,
  resolve unknown outcomes, clear barriers or adopt legacy mappings. Those
  contracts and the existing provider/rollout prerequisites remain blockers.
- Kommo's scalar PATCH, like its existing legacy scalar writes, does not provide
  documented remote CAS. Pre-read conflict detection cannot eliminate a remote
  edit between GET and PATCH. Narrow scalar payloads cannot alter relations, but
  a stronger guarantee against same-field remote races needs a provider contract.

No production policies or rights are changed, the global gate remains off, and
no production credentials or provider requests were used during development.

Primary API shapes checked:
https://developers.kommo.com/reference/update-contact
https://developers.kommo.com/reference/updating-single-lead
https://developers.kommo.com/reference/custom-fields

Tests include isolated PostgreSQL/WASM, mocked transport/API routing, rollback,
revocation, changed provider policy, connection generation, stale revisions,
unknown-outcome ordering and successive different-field edits. Real multi-session
PostgreSQL fence tests require a disposable localhost DSN; they are skipped when
unavailable, not represented as passing concurrency evidence.


## Legacy-to-scoped admission fence

Legacy delivery now owns a per-card PostgreSQL session advisory lock across the
provider call and its local result commit. Native human contact/deal updates,
when the scoped gate is enabled, take the matching transaction lock before any
tenant/member/card locks. Thus the first scoped mutation waits for a currently
running fenced legacy request; different cards can still proceed. Busy duplicate
workers do not send or finish another worker's claim. After admission, the worker
also validates the persisted processing state and attempt counter, not just an
expired lease.

New scoped-era claims atomically record `not_started` with the exact claim attempt
before worker admission. A busy card therefore leaves durable evidence that this
claim never sent. A later claim may renew only matching unsent evidence; it cannot
replace another sender's `sending`/`unknown` outcome or invent evidence for a
historical attempt. Send admission locks the outbox row while validating the
claim and committing possible-send evidence. An expired unsent Kommo create is
also safe to admit for its first actual send, unlike a possible earlier create.

In scoped mode, legacy possible-send evidence is committed before I/O. Exceptions,
process death and lost replies leave unknown/sending evidence, including when the
old retry scheduler later puts the event back into pending. Such an event cannot
be replayed, even after disabling the flag. A historical retry with no durable
send evidence is also unverified rather than replayed while scoped mode is on.
Unmarked gate-off records retain their existing successful exporter payload and
retry behavior, with same-card serialization added.

A first scoped local save with a processing/unknown legacy event, an attempted
pending legacy retry, or unresolved possible-send evidence reports
`needs_verification` / `scalar_legacy_outcome_unknown`. The scalar dispatcher
rechecks this before any provider call. Processing/pending claims with matching
durable `not_started` evidence are excluded, so ordinary card contention cannot
permanently quarantine an unsent event or block a new scoped scalar edit. Unknown
states and possible-send evidence are never excluded. Neither lease expiry nor a new local
revision proves that a remote operation has stopped. There is no automatic
recovery or clearing of this evidence.

This is a bounded fence for the native human update entry points and this legacy
delivery worker. Native stores open their own connections and do not accept an
existing transaction. Actorless service writes, imports and direct SQL writers
have not been globally converted or granted human authority. No lock is added to
their tenant/config-first paths. New/updated workers must be fully rolled out and
old in-flight workers drained before activation: an old worker cannot honor a
new advisory lock. Historical ambiguous operations still require an explicit
resolution contract. The fence is not remote CAS and does not prevent provider
users or other applications from editing concurrently.
