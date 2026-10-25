# Owner chat management: safe action matrix

| Need | Existing surface | Safety boundary |
|---|---|---|
| Read active knowledge, drafts, instructions, agent/channel state | Beinbot/MCP read tools | tenant + agent/channel OAuth scope; redaction remains active |
| Add owner-provided bootstrap knowledge | `administrator_draft_owner_knowledge_create` | verified owner only; versioned draft; marked `owner_provided`, unverified, inactive and unbound |
| Change behaviour, disabled triggers or restricted config | existing draft + preview/test tools | allowlisted scope, optimistic version and audit; never enables a trigger |
| Approve or publish/apply | existing authenticated owner web approval | exact draft/version receipt, expiry, single use and revoke; chat words are never approval |
| Consult a developer | existing internal consultation + verified Telegram binding | internal-only; delivery revalidates active support, binding and opt-in |

There is no trusted ChatGPT-chat approval transport. A chat may create or inspect a draft, but the owner must use the existing authenticated company approval screen for exact hash/version approval. This deliberately prevents customer or agent text from becoming an approval.

`owner_management_eligibility_read` is the read-only diagnostic: it returns
concrete `denied`, `pending`, or `uncertain` reasons without exposing customer
content or changing a queue. Existing resolver links may be used only after
the owner-scoped route resolver confirms the tenant/channel/conversation; a
missing case remains a no-case route, never a fabricated card.

Consultations remain the existing internal lifecycle: create → queued/waiting
→ owner/developer response → confirmed-review candidate. Delivery is optional
and revalidates binding, opt-in, recipient role and working hours; an answer is
not automatically a customer resolution or published knowledge.
