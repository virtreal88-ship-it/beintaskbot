# Support cases integration

Base: production main d752eff5. Sources: eb6187ca + 3118cad4 + 343c4b73.
No textual merge conflicts. Fixed semantic integration gaps: batch launch/poll/results,
tenant-wide queue, server keyset pagination/status filter, actual channel catalog,
clarification action and disabled-agent publication selection. UI dates are local
calendar dates, converted to UTC half-open intervals; end date is inclusive in UI.
Session changes clear review data and reject stale API responses.

Verification: 84 targeted Python tests (candidates, batches, routes, group history,
AI teams), two Node UI checks including mocked browser workflow. No production reads,
paid model calls or deployment. Existing /webapp source unchanged.

Schema adds extraction/candidate/publication/batch receipt tables and provenance
JSONB on Knowledge Sources; expands candidate status to needs_clarification. No
historical messages or agent configuration rewrite. Production migration not run.

Limits: manual batches are process-local, at most four fragments/320 messages;
restart is reported as interrupted and never auto-replayed. UI shows partial scope;
exact continuation cursor is currently API-only (UI can narrow the period).
No semantic deduplication of distinct incidents. Deterministic PII redaction is
conservative but cannot guarantee removal of every free-text identity. Publication
requires employee approval and evidence confirmation. Live browser/mobile and real
model/provider validation remain for pre-release review.
