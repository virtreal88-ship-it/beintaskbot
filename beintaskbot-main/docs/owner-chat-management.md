# Owner chat management: safe action matrix

| Need | Existing surface | Safety boundary |
|---|---|---|
| Read active knowledge, drafts, instructions, agent/channel state | Beinbot/MCP read tools | tenant + agent/channel OAuth scope; redaction remains active |
| Add owner-provided bootstrap knowledge | `administrator_draft_owner_knowledge_create` | verified owner only; versioned draft; marked `owner_provided`, unverified, inactive and unbound |
| Change behaviour, disabled triggers or restricted config | existing draft + preview/test tools | allowlisted scope, optimistic version and audit; never enables a trigger |
| Approve or publish/apply | existing authenticated owner web approval | exact draft/version receipt, expiry, single use and revoke; chat words are never approval |
| Consult a developer | existing internal consultation + verified Telegram binding | internal-only; delivery revalidates active support, binding and opt-in |

There is no trusted ChatGPT-chat approval transport. A chat may create or inspect a draft, but the owner must use the existing authenticated company approval screen for exact hash/version approval. This deliberately prevents customer or agent text from becoming an approval.
