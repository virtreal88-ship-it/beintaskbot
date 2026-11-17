# Complete the accessible dialogue queue

The three-dot list menu offers **Mark all dialogues completed**. It gathers all
pages of the current user's accessible queue, independently of search and list
filters, and deduplicates group source routes. An explicit read-only list preview supplies each eligible route's current
lifecycle revision and latest inbound ID in a tenant-scoped batch query.
Preparation never fetches conversation histories one by one, and a server that
does not acknowledge preview mode fails closed before confirmation. No chat is
opened or marked read while preparing the operation.

Confirmation states the exact number of eligible **dialogue sources**. Waiting
sources use the existing `dismiss` command (no fictitious acceptance or episode).
Only accepted sources owned by the current operator use `close`. Other operators'
accepted sources, non-Connect routes, unavailable sources and completed sources
are not modified. No case or AI operation is launched.

After confirmation the frozen batch is processed sequentially. Existing server
mutations lock each route, recheck permissions and revision, and verify the last
inbound ID for dismiss. They retain their idempotent receipts and audit events.
New messages or ownership changes cause stale commands to fail, not override
new work. No further routes are discovered after approval. Per-item conflicts
are counted as skipped; ambiguous/network failures stop the batch and report
unconfirmed plus not-processed counts. A session/tenant change stops the run.
No automatic retries or resume occur, and unread counters are untouched.

This is a non-atomic batch, not a database-wide update. A browser close may leave
already-confirmed items completed. A later explicit run reads a fresh snapshot.
Pagination failures or cycles abort preparation before any mutation.

Verification: `tests/test_dialog_lifecycle_bulk_ui.cjs` uses synthetic API data
for confirmation/cancel, repeated clicks, pagination/group deduplication, own vs
other operator, stale revision, revoked access, network ambiguity and identity
change. Existing lifecycle SQL/ownership/race tests remain the server contract.
Never verify this feature by bulk-completing a real customer's queue.
