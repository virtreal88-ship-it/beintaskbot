# Scoped asynchronous dialogue history merge

This is off unless both `DIALOG_HISTORY_MERGE_ENABLED=1` and
`DIALOG_HISTORY_MERGE_SCOPE=<tenant-uuid>:<source-uuid>` are explicitly set.
Both IDs must use canonical UUID spelling. Missing/malformed scope, wildcard
and multi-source configuration all fail closed. No actual tenant or source
is configured here. Remove either setting to roll back: no more claim,
probe or projection after previous instances have drained. Already stored audit
and job records remain. Each instance stops this worker on application cleanup.
An interrupted proof leaves its original short lease to expire, and a retry
of that same job checks current exact scope again. Never delete original rows
or force a new job while rolling back.
It does not copy history, write provider aliases, move attachments/episodes/cases,
change CRM records, or call a sending API. No new service is required. The
ordinary release is dormant. Activate a one-source pilot only after exact
release CI, independent review, verified dormant Worker deployment with old
workers drained, and explicit operator approval of that tenant/source.
Verify the exact active source belongs to the approved tenant and is already
using the native WhatsApp route. Its existing
`BEINTASKBOT_UNIFIED_WHATSAPP_SEND=1` is a prerequisite; never change that or
another transport flag as part of this release. Set only the reviewed merge
variables. No identifying production values belong in source control.

A healthy Worker alone does not attest readiness. A schema startup failure
logs `Dialogue history merge schema preflight unavailable`; there is no
positive startup-ready log. After the scoped deployment has replaced all
old workers, verify the approved active owner's refreshed profile advertises
`dialog_history_merge:true` and the matching `dialog_history_merge_source_id`.
That checks exact source/update authorization, the optional merge schema and
the indexed history prerequisite without enqueueing anything. Initial
activation and read-only verification must not merge real chats.

A same-source direct pair is read and reauthorized before enqueue. Only one job
per explicitly scoped source can be pending, and leases limit the platform to one provider proof at
a time. The worker calls the existing WebCall exact LID contact lookup only after
an explicit start, without holding ownership or source locks over I/O. The
number must match the entire primary phone and the current exact gateway scope.
Names, suffixes, owner-entered display numbers and missing phone are not proof.
No channel inventories or historical message bodies are read in the queue.

The owner chooses the display name and number independently in the two-route
comparison. The preview returns only current exact-route metadata and an
opaque revision that binds those options. Create accepts only offered lead
IDs (or no value), revalidates the revision and snapshots the chosen display
values on the job. Another request ID cannot change a running/completed
selection. The selected name and phone are owner-visible only on a valid
merged projection with both exact routes authorized; they never change the
original contact, proof lookup, phone identity, replies, or route. As with
other project data, no chosen values are written to the audit payload.

The progress bar counts three finished checks: exact scope/lifecycle checks,
current device proof, and committed publication. It does not imply a message
copy count. The joined view consumes chronological, bounded pages on demand
from the retained routes. Close does not cancel; explicit cancel succeeds only
before publication. A separate undo removes the presentation join; it does
not alter original routes. A channel/gateway change or contradictory evidence suspends the joined
view. The conservative non-lifecycle gate also suspends it after a new workflow.

The proof-only mode blocks any affected lifecycle workflow or episode,
including closed histories. Lifecycle-bearing pairs require the separate
exact-route projection described below; the original physical merge guard
still applies.

## Required semantics for lifecycle-bearing consolidation

A pilot explicitly approved for lifecycle-bearing pairs requires
`DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED=1` in addition to both scope settings.
It enables independent route visibility and exact action selection during
lifecycles. It does not admit a pair without current exact provider proof.
Stock installations and the base proof-only mode remain unchanged.
The opted-in All list also combines mixed active/archive cards if both routes
are independently present and authorized. A separate exact history navigator
labels the closed route read-only; it cannot transfer buttons, receipts,
completion, sending or counts from the active route. Active and Completed keep
their own filter results. Older clients without the navigator continue to list
mixed routes separately. A single authorized physical route remains unchanged.

The existing physical-merge path in `tenant_connect_dialog_equivalence.py` checks
both exact routes (including existing aliases) for workflows and episodes. Do not
remove that guard. The existing paired-history reader also prohibits workflows
and inbound history on the companion. Those requirements are not evidence that
both real histories may share a send route, active operator, or case.

The lifecycle projection uses a read-time card identity over an atomically
activated proved pair, **not** an alias or replacement of the two dialogue IDs.
Compute the card from the actor's ordinary list visibility, keeping an actionable
route visible to its assigned operator. For every initial history read and older
page, resolve the same actor and exact source, authorize each physical route in
the correct list/history mode, and pick/filter the timeline from the authorized
subset only. If both exact routes are actionable, the card needs an explicit
inner route chooser for route-bound actions; choosing a route never reassigns or
makes other history writable. If only one is authorized, neither the other raw
route, its counters, timestamps, attachments, quote targets, nor case information
may be exposed. Historical messages keep their origin metadata and permissions.
Reply, accept, complete, read markers, attachments, references, and exports
must use the original exact route/episode applicable to that operation.
Undo only removes the presentation join, keeping both original routes intact.
The known old self-only provider receipt is accepted only if it is strictly
bracketed by signed confirmations of the exact same phone/LID on the same
route, all older than its current device proof. An orphaned summary, missing
confirmation, conflicting alias, new contradiction or evidence overflow
blocks the join. Compare/search never invokes this worker or loads bodies.

The first pilot can have at most 100 active presentation joins for its one
configured source. An additional pair is refused before work starts (and
checked again before publication), not allowed to disrupt previously visible
joined cards. This does not cap the count of messages in any timeline.

Display choices are owner-only presentation, not proof. Saving a different
display name or clearing a number after comparison but before publication
requires a fresh comparison. A later edit also invalidates the stale display
snapshot for existing joins; it does not reopen, undo or reroute the history.
Provider reactions stay bound to the currently selected actionable physical
route; the companion and passive lenses never acquire reaction authority.

Required release fixtures:

| Fixture | Required result |
|---|---|
| Both active with different operators | Each sees one card with only their own authorized timeline/actions. An owner with separately authorized passive visibility sees only what that mode authorizes, never another operator's controls. |
| Both active with the same operator | One card and complete chronological permitted history, with explicit route choice for ambiguous replies/actions and independent closes/cases. |
| Closed archive plus active accepted / both archived | One card where both belong to the same permitted filter; status and counts describe visible qualifying routes, not a synthesized workflow. Archive history is read-only and cannot substitute a reply target. User initiates exact lens selection; no sending or auto-read via the navigation. |
| Member/source revocation or transfer during a page | Reauthorize under stable exact route ordering; remove no-longer-authorized data, scrub cached history/quotes/media, and ignore outstanding older responses from the old authorization. |
| Stale or conflicting canonical/provider evidence; wrong tenant or channel | No join, no cross-route action, no identifying details and no silent matching by name or number suffix. |
| Manual name/phone edited before or after activation | Block pending publication if a displayed choice changed; after publication keep the merged history while dropping the outdated display snapshot. |
| Large, interleaved history with equal timestamps | Indexed bounded reads, stable no-gap/no-duplicate keysets, no pre-click content loads, and no bulk copy or locks during provider I/O. |
| Undo/queue cancellation vs projection read or new inbound | Only committed states are visible; no underlying rows, messages, case IDs, episodes, or attachments move or disappear. |

Verify these cases under disposable native PostgreSQL with real concurrency
and Chromium before pilot activation. No real pair may be activated without
a later explicit merge confirmation and exact current proof.
