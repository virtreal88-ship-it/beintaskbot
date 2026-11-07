# Completed approval notifications

The notification center excludes old task creation/completion approval requests
from its unread list and unread count only when the tenant's exact saved approval
event resolves to one command, the event was expanded, and that command is now
`done` or `rejected`. The key is matched exactly, not by title, URL or substring.
Because historical keys omit creator identity, multiple matching creators are
ambiguous and remain visible, even if all their commands are terminal.

Missing storage, missing evidence, other tenants, other event types, accepted but
unfinished work, uncertain delivery and unrecognized states do not suppress a
notification. There is no data deletion, synthetic read receipt, new migration or
change to task state. The All view retains every authorized historical row, and
an employee may still explicitly mark a historical item read. The unread count
returned after marking any item read uses the same current-state predicate.

This change does not suppress handoffs, developer replies, dispatch failures,
owner risk alerts or any other notification source. PR #540 integration should
retain its risk-specific joins and append this independent proof/column to the
shared notification eligibility projection. Neither feature replaces the other.

Behavioral tests live in `tests/test_tenant_notification_center.py`; the existing
native PostgreSQL test module imports the same contracts. Local isolated setup:
`npm install --prefix .support-test-deps @electric-sql/pglite jsdom@26 playwright`, then
`python -m pytest tests/test_tenant_notification_center.py -q`.
Native-driver checks additionally require the disposable `BEINTASKBOT_TEST_POSTGRES_DSN`
configured by the repository's isolated database guard; never use production.
