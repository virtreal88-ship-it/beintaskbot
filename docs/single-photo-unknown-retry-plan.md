# One owner-authorized retry of an unknown media attempt

Implemented as an optional extension of the existing owner component; no
runtime consent is created and no retry runs on deployment or read. Review
and a fresh authenticated owner decision are required before a paid retry.

Call `administrator_single_message_extraction_review` with its existing
`message_id`, `channel_id`, `lead_id` plus `retry_of`, exact `source_version`
and a unique `request_id`. All three retry fields are required together.
Only a terminal unknown **image** attempt from the existing single-message
owner flow is eligible. Its source, original byte hash and unknown cache token
must still be verifiable; missing legacy evidence fails closed.

The same existing app-only completion requires the owner's explicit
`acknowledge_prior_unknown=true` checkbox. It queues one new fragment through
the ordinary worker, with a ten-minute consent expiry. Repeated completions
return the same receipt, including after expiry; they do not queue another.
Stopping the new campaign or revoking its OAuth authorization blocks dispatch.

The existing single-message owner consent has a one-use retry action and
creates no persistent publishing or spending permission. It binds the approval
to the prior extraction ID, the same message/channel/tenant/owner, exact media
version and body hash, agent/model/config hash, and the existing media policy.
The authenticated owner action must explicitly acknowledge that the previous
provider outcome and charge are unknown. Chat text is not a backend capability.

Approval is consumed and one child attempt queued atomically under the existing
campaign lock, with a persisted prior-attempt link and request idempotency key. Repeat
submissions return the same child receipt; an unknown child cannot create a
second child. Keep the previous extraction, cache entry, and all monetary holds
unchanged. Reject stale source/config, foreign tenant, revoked/expired approval,
and any parent outside the exact approved source.

The existing source and cache guards may admit this child only after checking
that consumed authorization. Its cache attempt namespace must be derived from
the validated authorization, never from a caller-supplied new source ID. Reuse
successful existing observations; only the explicitly approved unknown operation
can make a new call. Revalidate before each provider boundary.

Use the existing atomic spend ledger for a fresh bounded reservation, counting
the old unknown holds against the unchanged tenant cap. The last observed photo
tariffs reserved 3,480 microUSD for text and 39,120 for vision (42,600 total);
recompute and display the exact bound at approval time rather than treating
those amounts as a permanent tariff. A price/config change invalidates approval.
No reserve release, status reset, automatic background retry, or bulk history run.

Regression tests cover duplicate/parallel approval and claim, same-source/version fences,
cross-tenant denial, revocation between enqueue and dispatch, insufficient budget
including old holds, cache reuse, one child only, and an unknown child with no
automatic retry. Execution requires review of this supported path first.
