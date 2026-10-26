# One owner-authorized retry of an unknown media attempt

Proposal for review; this change does not enable or execute retries.

Extend the existing single-message owner consent flow with a one-use retry
action, not a persistent publishing or spending permission. Bind the approval
to the prior extraction ID, the same message/channel/tenant/owner, exact media
version and body hash, agent/model/config hash, and the existing media policy.
The authenticated owner action must explicitly acknowledge that the previous
provider outcome and charge are unknown. Chat text is not a backend capability.

Consume the approval and enqueue one child attempt atomically under the existing
campaign lock. Persist the prior-attempt link and request idempotency key. Repeat
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

Required tests: duplicate/parallel approval and claim, same-source/version fences,
cross-tenant denial, revocation between enqueue and dispatch, insufficient budget
including old holds, cache reuse, one child only, and an unknown child with no
automatic retry. Execution requires review of this supported path first.
