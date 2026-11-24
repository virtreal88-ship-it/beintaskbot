# Dialogue completion and company links

The individual **Complete** button closes the current operator's accepted episode
with `company_choice: "unchanged"`. The server validates the exact tenant, source,
conversation, operator and revision. The receipt updates the open chat from its
current contents. Company selection remains available in the Company panel.

`TENANT_DIALOG_COMPANY_COMPLETION_REQUIRED=true` enables the company check
for bulk completion and legacy clients using the older close contract.
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

The Company panel stores the selected Operator account against the exact dialogue
in `saas_operator_dialog_accounts`. The server repeats the original bounded
Operator search and checks for the exact selected account ID. The association
records the verified connection generation and instance. CRM company sync runs
through the Company page. Historical CRM links stay in their original tables.

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
