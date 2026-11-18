# Support case chat audit (2026-11-18)

## Verified causes

- The case consultation form initialized both range selectors to the final message. The server limit was 30, not one. The case form now selects the entire proven accepted cycle through case creation automatically. The revised user requirement removes message-count caps; a paginated preview does not truncate the share.
- The consultation episode fence rejected another recipient for an overlapping fragment. Explicit case-bound sends now have independent recipient scopes; same-target uncertain delivery and retry fences remain.
- The old case detail mixed editable fields, unrestricted conversation history, AI analysis, attachment pickers and Operator context. The requested removed panels and their UI handlers are retired; stored data and server APIs are not destructively deleted.

## History and safety

New cases from an accepted dialogue carry explicit episode ID/revision and frozen membership under the lifecycle lock. Creation does not close or transfer the dialogue; stale, foreign-owner and cross-cycle requests are rejected. Legacy history is bounded by a uniquely identified episode active at case creation, from acceptance to case creation. Unknown/overlapping acceptance evidence is labelled explicitly. Existing saved memberships are never widened. Later imported messages and post-creation messages cannot expand the frozen history. New cases save the full proven interval; history is paginated for loading while public sharing retains the complete frozen membership.

Developer threads are grouped by tenant-authorized destination channel and conversation. Outgoing bodies are the actual durable delivery bodies, not regenerated summaries. `sent` means submitted to the provider, not delivered/read. `unknown` is not retried. Incoming replies require a proven quoted request or explicit consultation marker; unrelated group messages are not silently attributed to a case.

## Operator task creation: external contract blocker

Existing `task_creation_contract()` reports disabled because authoritative response identifier, idempotency, timeout reconciliation and public tracking link contracts are missing. `ticket_draft_handler(action=create)` fails with `operator_task_create_contract_unconfirmed`.

The original `API_DOCUMENTATION.md` section 4.1 describes `POST /task/create` with title, description, optional account and priority. It does not document the response, idempotency key, reconciliation or public link. `/task/list` is filtered to the current operator and does not establish lookup by a unique creation request. `/task/listExternalDone` example identifiers do not prove the create response. The referenced OpenAPI file was not found alongside the supplied document.

Required before enabling: confirmed authorization scope, account binding, request identity/idempotency semantics, create response containing the canonical issue/task ID, a lookup to reconcile ambiguous outcomes, and the public tracking link. Do not bypass Operator by creating duplicate Linear issues directly.

## Scoped media follow-up

Text follow-ups reuse the existing durable consultation delivery mechanism. Inline files/voice bind the existing media/voice receipt pipeline to the case before dispatch, revalidate case/destination rights within guarded dispatch, and reconcile provider IDs into case reply correlation. A check-before/send/record-after wrapper is not safe. The full developer-dialog link is a real fallback, not a claim of completed inline functionality. The new CASE_CHAT_MEDIA_ENABLED flag gates only this case-bound path; existing media/voice flags and group-voice restrictions remain unchanged. Exact-head CI is required before enabling it.
