# Support case chat audit (2026-11-18)

## Verified causes

- The case consultation form initialized both range selectors to the final message. The server limit was 30, not one. The case form now selects the latest 50 eligible case messages automatically, with the full case count shown when truncated.
- The consultation episode fence rejected another recipient for an overlapping fragment. Explicit case-bound sends now have independent recipient scopes; same-target uncertain delivery and retry fences remain.
- The old case detail mixed editable fields, unrestricted conversation history, AI analysis, attachment pickers and Operator context. The requested removed panels and their UI handlers are retired; stored data and server APIs are not destructively deleted.

## History and safety

History is bounded by a uniquely identified episode active at case creation, from acceptance to case creation. Unknown/overlapping acceptance evidence is labelled explicitly. Existing saved memberships are never widened. Later imported messages and post-creation messages cannot expand the frozen history. New cases save the full proven interval; history pagination and public sharing have separate limits.

Developer threads are grouped by tenant-authorized destination channel and conversation. Outgoing bodies are the actual durable delivery bodies, not regenerated summaries. `sent` means submitted to the provider, not delivered/read. `unknown` is not retried. Incoming replies require a proven quoted request or explicit consultation marker; unrelated group messages are not silently attributed to a case.

## Operator task creation: external contract blocker

Existing `task_creation_contract()` reports disabled because authoritative response identifier, idempotency, timeout reconciliation and public tracking link contracts are missing. `ticket_draft_handler(action=create)` fails with `operator_task_create_contract_unconfirmed`.

The original `API_DOCUMENTATION.md` section 4.1 describes `POST /task/create` with title, description, optional account and priority. It does not document the response, idempotency key, reconciliation or public link. `/task/list` is filtered to the current operator and does not establish lookup by a unique creation request. `/task/listExternalDone` example identifiers do not prove the create response. The referenced OpenAPI file was not found alongside the supplied document.

Required before enabling: confirmed authorization scope, account binding, request identity/idempotency semantics, create response containing the canonical issue/task ID, a lookup to reconcile ambiguous outcomes, and the public tracking link. Do not bypass Operator by creating duplicate Linear issues directly.

## Media follow-up

Text follow-ups reuse the existing durable consultation delivery mechanism. Inline files/voice require binding the existing media/voice receipt pipeline to the case before dispatch, revalidating case/destination rights within guarded dispatch, and reconciling provider IDs into case reply correlation. A check-before/send/record-after wrapper is not safe. The full developer-dialog link is a real fallback, not a claim of completed inline functionality. Implementation and verification of this extension must be completed separately before marking the user's complete chat request done.
