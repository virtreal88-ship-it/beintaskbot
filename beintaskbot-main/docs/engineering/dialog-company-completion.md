# Company required before completing a human dialogue

This is a default-off BeinSystems pilot, for the existing durable Connect dialogue
lifecycle. `TENANT_DIALOG_COMPANY_COMPLETION_REQUIRED=true` adds the company
check only to a fresh explicit Close of the current operator's accepted episode.
Accept, Start, existing replies (including unlinked accepted episodes), inbound,
history, and transfers do not require a company. No reply needed remains a
distinct action on waiting messages; it cannot close an accepted episode.

Completion uses only the exact tenant/channel/canonical conversation's current
local CRM company binding. A valid existing company is not disclosed to operators
without CRM rights; they may complete the episode without changing the company.
If unbound, the operator may explicitly select an existing active company only if
their current dialog/source/ownership and CRM company view + update access permit
it. A proven same-tenant archived binding may be explicitly replaced in the same
close; a live, foreign or unknown binding cannot be overwritten. The selection,
binding, close, receipt and audit share one transaction. A 422 means the open
dialogue and existing messages are not changed. Exact authorized retries recover
their existing receipt; stale revisions cannot rebind or re-close a route.

The inline picker works on ordinary and group Connect routes using the existing
CRM catalogue permissions, and never matches by name automatically. It does not
create companies, change employee rights, call Operator, send anything, store a
draft or perform schema migration. Group links belong to one exact source; one
source's proof cannot complete another. A member who lacks existing CRM access
cannot choose, and the dialogue stays open. Unresolved identity and unknown
existing binding require separate authorized repair.

With the rule on, the read-only Complete All preview finds accepted owned routes
with a currently active local company link. It does not verify each dialogue
address in the preview. The confirmation says **up to** this many; any close
that cannot verify the binding or identity stays open and is counted. Waiting
routes stay out of bulk for an individual No reply needed decision. Every actual
close rechecks under the canonical route lock. Turning the rule off
restores the previous completion flow without dropping any links or companies.
Do not activate without validating existing schema, workspace scope, real role
coverage, group-source paths and current browser assets. Connect's existing
identity folding does not fold this company binding table: verify retained raw
aliases have no orphaned or contradictory bindings. Unresolved alias bindings
block activation; do not auto-merge or rewrite them.

Deploy with the flag off; confirm the target workspace uses updated workers
and refreshed browser assets before a controlled opt-in. With the flag on, a
new waiting-item dismissal from an old client (individual or bulk) stays open
with a refresh prompt. Only the new individual control identifies its action.
Previously committed identical receipts replay; latest inbound and permissions
still apply to new requests. If a source grant is revoked after preview, the
mutation skips that route under current grants. Kommo and retrospective
historical remediation are outside this slice. Operator account import is separate.
