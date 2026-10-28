# Direct conversation duplicates: 2026-10-28

The supplied screenshot shows two Connect conversation cards with the same
avatar, one titled with a phone and another with a name and channel subtitle.
The last-message texts differ and the timestamps are eight seconds apart.
This is a conversation-list duplication report, not identical message text.
The UI shows a channel subtitle when `phone` is empty, consistent with an
unlinked LID. A screenshot cannot prove tenant/channel IDs or a JID/LID pair.
The exact production pair has not been established: previously denied live
viewer/browser access was not retried or bypassed.

## Reproduced defects

Base commit: `377ff9941db9839e49014366debb147bcd51e5bf`.

1. An unpaired LID receipt creates a conversation. A later signed JID/LID pair
   checks aliases but never looks for that existing conversation, chooses JID,
   and creates another conversation/lead. Replaying the initial receipt then
   fails `message_scope_conflict` because its message belongs to the old lead.
2. A nonempty partial `directIdentity` skips the stored-alias lookup entirely.
3. `SELECT FOR UPDATE` cannot lock missing aliases. Selection before the
   dialogue lock and unchecked `ON CONFLICT DO NOTHING` permits inconsistent
   decisions between workers.
4. The equivalent `@c.us`/`@s.whatsapp.net` domains produce separate routes.
5. A new phone paired with an already mapped LID can silently join the existing
   person. Conflict metadata must not authorize identity linking.

The first 11 new regression cases produced **10 failures / 1 pass** on the
unchanged resolver, including an actual multi-process webhook race. Those
cases pass with the fix. Additional cases cover consequences and isolation.

## Durable identity contract

The key is `(tenant_id, connect_channel_id, provider identity)`. Phone-domain
aliases represent one phone; linking a LID requires a valid signed provider
pair containing the delivered conversation. Names, avatars, message contents,
mutable phone labels, and proximity in time are not identity evidence.

Sorted PostgreSQL transaction advisory locks reserve all involved identity
keys before reading aliases, including keys with no row yet. The existing
alias primary key and deferred conversation foreign key persist the result;
the winner is checked after insertion. Locks, alias records, conversation,
message and webhook receipt share the transaction. Only confirmed transaction
rollback errors are retried automatically; an uncertain connection outcome is
left for safe provider replay.

An existing sole conversation keeps its ID and lead. A newly discovered phone
does not replace a canonical LID. Verified phone evidence is separate from
route identity, so native contact links, employee recognition and phone
triggers retain their meaning. Historical message IDs, media results, raw
receipts, assignments and manual hold remain attached to the same route.

The automatic mapping permits one phone class and one LID. A conflicting
second phone/LID or independently existing routes are quarantined for review.
An additive `saas_connect_identity_conflicts` table records first conflict
evidence even on event/message replay, without replacing the original message.
Conflict checks follow scoped aliases, including both canonical LIDs of a
contradictory pair, and the list exposes the same blocked-assignment reason.
The canonical lookup has a tenant/channel/canonical index.

## Creation paths and boundaries

Production conversation inserts occur only in `_upsert_conversation`, called
by signed inbound and observed outbound ingestion. Both use the resolver.
CRM send receipts, AI send receipts, reconnect configuration, avatar loading,
list refreshes and browser clicks do not create another conversation route.
The existing explicit reconciliation tool can restore/migrate historical
routes under table locks; it is not invoked by this fix.

Message deduplication remains by the durable Connect message ID, with webhook
event receipts and route validation. Two legitimate messages with equal text
remain two messages. Different tenants or connected numbers remain separate.
The patch does not introduce content-based deduplication or hide list rows.
Observed outbound retries with a new event envelope and repeated CRM send
receipts now apply operator takeover only on the first message insertion.
Both previously repeated that side effect; two new regression tests reproduced
it before the fix. A distinct legitimate send still applies takeover normally.

## Validation and deployment limits

`tests/test_connect_identity_ingestion_postgres.py` runs on disposable local
native PostgreSQL. It covers the signed HTTP webhook-to-list-to-history journey
(including rejection of a bad signature); late and
partial pairs; both phone domains; restart/new-envelope replay; concurrent
webhooks; both ordered inter-process lock waits; transaction rollback and a
lost response after commit; contacts, employees, media/analysis preservation;
tenant/channel separation; and durable contradictory-pair quarantine.
No external send or paid generation is used.

`.github/workflows/connect-direct-identity.yml` defines the full targeted
regression command, including existing identity, reconciliation, rollback,
multi-process, native-contact and operator-list tests.

Local validation before the outbound replay addition: the full targeted
command passed **180 tests** after rebasing onto `05eb3ba` (the parallel case
workflow release). Operator auto-refresh JavaScript regression also passed.
Two further outbound replay regression cases are included in the final suite.

Broader diagnostic runs also found pre-existing failures, reproduced with the
same commands on clean base `377ff99`: all four tests in
`test_group_coordination_multiprocess.py`; four positional cursor-mock tests
in `test_tenant_connect_handoff.py`; and the manual-worker / foreign-anchor
expectations in `test_connect_group_history.py`. They are not hidden by changes
to production logic or silently described as passing.
The static `test_tenant_operator_dialogue_regressions.cjs` media-preview regex
also fails unchanged on the base; the relevant frontend source is untouched.

No production rows are deleted or merged. Already split histories require
their own verified reconciliation, preserving dependencies. If neither event
nor stored aliases proves that two IDs are one person, the application must
retain separate routes rather than guess. This is the explicit limit of the
no-duplicate guarantee. The screenshot's existing pair and live LID sending
have not been verified in production; a green build is not proof that that
specific pair has been repaired.

This change touches transport/identity, contact linkage, employee recognition,
and the backend list conflict snapshot. It does not edit campaign/case UI or
the parallel campaign work.
