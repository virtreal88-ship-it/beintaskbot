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

The MCP adapter exposes an app-only resource and returns its random capability
only in component result metadata—never in tool text, structured state, a URL,
or audit payload. The server binds it to tenant, owner, OAuth connection, exact
draft version/hash, target and expiry. Its future manual decision bridge is
disabled by default, retains the existing Origin and owner-session checks, and
consumes the capability atomically before calling the existing approval logic.
Until an actual Dot/Android host preserves component metadata and can be
validated, its Approve/Reject controls remain disabled and the web review is
the only execution path. This adapter cannot handle prompt, trigger or
configuration drafts; an approval receipt also never bypasses
`AI_AUTONOMOUS_APPLY_ENABLED`.

`owner_management_eligibility_read` is the read-only diagnostic: it returns
concrete `denied`, `pending`, or `uncertain` reasons without exposing customer
content or changing a queue. Existing resolver links may be used only after
the owner-scoped route resolver confirms the tenant/channel/conversation; a
missing case remains a no-case route, never a fabricated card.

Consultations remain the existing internal lifecycle: create → queued/waiting
→ owner/developer response → confirmed-review candidate. Delivery is optional
and revalidates binding, opt-in, recipient role and working hours; an answer is
not automatically a customer resolution or published knowledge.
