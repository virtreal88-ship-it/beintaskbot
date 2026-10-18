# Owner-confirmed test continuation

The existing admin diagnostics preview can offer closing up to five old unknown runs
only on the explicitly selected personal Connect channel. Requires Human state,
Manual primary agent, no outstanding jobs/drafts, no run result, no matching current
job or any draft, and run age greater than three minutes. It is not send reconciliation.

The owner explicitly attests that this is their test dialogue. Under the existing
lock and CAS snapshot, one transaction adds a no-retry closure marker to the runs,
records their IDs in the audit, archives the legacy snapshot and grants the existing
channel-local permit. Unknown state and budget accounting are preserved. No records,
messages or other-channel states are deleted or moved. Pending drafts are never sent
or silently discarded: they block this action.

Resume is separate. Existing permit timestamp and resumed_at guards prevent old
messages (including repeated webhooks) from producing work. A changed snapshot or
concurrent administrator requires a fresh preview. No schema migration is needed.

UI: Diagnostics > Continue on existing channel > select channel and Manual primary
> Save permission > explicitly confirm ownership/abandonment. Refresh; Resume is a
separate administrator action. No production permission is granted by this commit.
