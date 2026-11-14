# App board moves: boundaries

Task moves change the deadline, not the completion status. The new board's date
ranges differ from the legacy webapp's calendar-week ranges. A drop opens a
precise deadline for review; a range never silently picks an unspecified day.
The same action is available from a keyboard/touch-friendly select. Completed
and overdue columns are not drop targets. Existing completion workflows remain
separate.

Native task moves use the existing update endpoint with `expected_updated_at`
and a deadline-only patch. The controller checks the current source, identity,
row capability and visible page. It keeps cards and filters mounted while saving.
A failed or ambiguous response never triggers another write automatically.

Kommo deadline changes use the explicitly authorized integration capability,
not the native-task edit permission. Stage changes are restricted to assigned
funnels; a creator-owned deal outside those funnels does not grant movement.
Existing terminal-stage approval rules remain in force. Legacy `/api/action`
is never used as a tenant-scoped substitute. Commands use conflict detection,
single-attempt transport, and durable unknown-outcome handling. An uncertain
command exposes read-only verification rather than another provider write.

Task client fields are read in a single bounded cached lookup. Native fields
follow customer-view scope; Kommo fields require access to the related deal.
A creator-owned task alone does not reveal a client from an inaccessible deal.
No additional provider request is made.
