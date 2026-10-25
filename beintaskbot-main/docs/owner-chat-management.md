# Owner chat management: safe action matrix

| Need | Existing surface | Safety boundary |
|---|---|---|
| Read active knowledge, drafts, instructions, agent/channel state | Beinbot/MCP read tools | tenant + agent/channel OAuth scope; redaction remains active |
| Add owner-provided bootstrap knowledge | `administrator_draft_owner_knowledge_create` | verified owner only; versioned draft; marked `owner_provided`, unverified, inactive and unbound |
| Change behaviour, disabled triggers or restricted config | existing draft + preview/test tools | allowlisted scope, optimistic version and audit; never enables a trigger |
| Prepare an owner-review component | app-only MCP adapter | exact knowledge/instruction/support-case version hash is bound to a short-lived opaque capability; it remains disabled pending trusted-host validation |
| Approve or publish/apply | existing authenticated owner web approval | exact draft/version receipt, expiry, single use and revoke; chat words are never approval |
| Consult a developer | existing internal consultation + verified Telegram binding | internal-only; delivery revalidates active support, binding and opt-in |

There is no trusted ChatGPT-chat approval transport. A chat may create or inspect
a draft, but the owner must use the existing authenticated company approval
screen for exact hash/version approval. This deliberately prevents customer or
agent text from becoming an approval.

The MCP adapter exposes an MCP Apps resource (`text/html;profile=mcp-app`) and
returns random capabilities only in component result metadata—never in tool
text, structured state, a URL, or audit payload. The server binds every
capability to tenant, current owner, live OAuth token/connection, exact draft
version/hash, target, action and expiry. The launcher is model-visible; the
completion tool is app-only and derives its connection from the validated
bearer rather than accepting a UI-provided connection id. It re-checks token
expiry/revocation/scope, owner authority, agent/channel scope and the draft in
the decision transaction; one choice consumes all sibling capabilities.

This is a test-only transport behind `AI_OWNER_COMPONENT_APPROVAL_ENABLED`.
With that flag off it does not advertise the completion tool; a narrowly scoped
pending-review launcher is still available only when administrator drafts are
enabled. It neither weakens the existing CRM owner-session/Origin approval API
nor treats a ChatGPT click as a signed human attestation. Dot/Android host
acceptance and metadata preservation remain untested, so production enablement
requires separate approval. The adapter cannot handle prompt, trigger or
configuration drafts; an approval receipt also never bypasses
`AI_AUTONOMOUS_APPLY_ENABLED` or publishes/binds material.

`owner_management_eligibility_read` is the read-only diagnostic: it returns
concrete `denied`, `pending`, or `uncertain` reasons without exposing customer
content or changing a queue. `material_kind` can return the distinct
knowledge/instruction/support-case publication prerequisite. No direct card or
conversation URL is emitted here: this repository exposes no suitable
owner-scoped resolver contract for that purpose. Existing resolver links may be
used only after the owner-scoped route resolver confirms the
tenant/channel/conversation; a missing case remains a no-case route, never a
fabricated card.

Consultations remain the existing internal lifecycle: create → queued/waiting
→ owner/developer response → confirmed-review candidate. Delivery is optional
and revalidates binding, opt-in, recipient role and working hours; an answer is
not automatically a customer resolution or published knowledge.
