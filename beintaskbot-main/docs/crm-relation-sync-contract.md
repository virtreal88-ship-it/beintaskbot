# Inactive relation synchronization contract

`tenant_crm_relation_contract.py` defines the immutable intent shared by the
implemented identity, journal and dormant delivery layers. Additive schema,
transactional storage and a conditional-provider test driver now exist. No
production relation adapter or scheduled relation-delivery caller is enabled.
The local routes retain `scoped_relation_sync_not_configured` before a mutation
that would require unsupported outbound relation synchronization. No real
provider calls were used to develop or test these layers.

## Implemented, independent of provider semantics

- An immutable, versioned `RelationIntent` records the exact human actor, source
  card, source `update` action, changed row, previous and new targets, and the
  timezone-aware post-change local revision. It represents the existing local
  deal/contact and contact/company operations, including both source-card
  directions. It does not authorize them.
- External identities carry the tenant, provider, provider account and
  connection generation. Mixed bindings, unrelated source cards, incorrect
  reverse-card unlink, identity collisions and no-op local changes are rejected.
  Account and generation values are server-resolved identifiers, never tokens.
  The database mapping layer implements this shape. Existing Kommo reads may
  register only fresh contact/deal INSERTs using the actual post-refresh account
  and connection generation. Historical IDs, phone/email matches, companies,
  outbound creates and BEIN are not thereby verified.
- Strict serialization accepts only that shape. Full record fields, credentials,
  provider request bodies, unknown versions and non-update actions are rejected.
  A canonical content fingerprint distinguishes changed intent under the same
  event ID. It is not a provider idempotency key or an exactly-once guarantee.
- An optional `ProviderExpectation` records an adapter contract version and
  digests of the complete expected and desired remote relation states. It has no
  default inferred from the local FK. An adapter must include cardinality and
  relevant metadata such as every Kommo contact and its main-contact flag.
- A pure observation classifier rejects evidence for a different event,
  account/generation or adapter contract. Missing observations stay unknown;
  divergent states are conflicts. An unchanged read after a timeout or an
  acknowledged request does not prove safe replay. Expired leases are not
  accepted as proof that a request was never sent.

`expected_observed` and `desired_observed` describe evidence, not permission to
send or proof this event succeeded. In particular, a desired read-back does not
prove this event caused the state or resolve an in-flight request. No classifier
result sends, retries, marks an event synced, or deletes an event.

## Agreed local semantics and current implementation

- Beintaskbot's client is the main Kommo contact. Replacement or unlink must
  preserve secondary contacts. This product choice is settled; provider behavior
  for promotion, an absent main and concurrent changes still needs verification.
- Link/unlink requires U of the card from which the action is made, including
  company/deal links. Initial deal creation may select a contact with current R.
  Local authorization and UI implement these rules without granting target U/D.
- The current immutable relation intent and conditional test driver cover
  `deal_contact` and `contact_company` under source U. Company/deal delivery and
  initial creation require deliberately extended contracts; local implementation
  does not imply provider delivery is implemented.
- Journal writes, local revisions, identity bindings, current source authority,
  supersession and uncertain outcomes have isolated SQL and mocked-provider
  coverage. A durable read-only card history exposes only safe recorded outcomes
  under current source R. It does not resolve uncertainty or certify remote state.

## Required before connecting this to live operations

1. Verify each provider's exact relation-only write, clearing, observation and
   completion guarantees. Kommo documents exact link/unlink endpoints, not a
   conditional main/company write. GET/write/GET cannot detect every intervening
   main change. A mock's atomic compare/apply is not a real provider capability.
2. Do not assume unlinking the main leaves a nonempty set without a main, or that
   promoting an already-linked secondary has the modeled result. Reconcile that
   contract with legacy readers which fall back to the first contact. BEIN
   relation-only, null-clearing and idempotency semantics remain unverified.
3. Complete trusted historical/company/create/BEIN mapping verification without
   adopting legacy IDs or guessing current connection generations. Fresh-import
   registration is not a bulk backfill or a general owner-verification workflow.
4. Add a production adapter and application wiring only when the required
   relevant-main/company condition can be preserved. Revalidate current source U,
   policy, tenant/member/department, mappings and connection at every admitted
   step. Never route relation intents through the actorless full-record exporter.
5. Preserve per-step request evidence, including acknowledged but unverified and
   partial operations, independently of an unknown HTTP outcome. Define explicit
   conflict/unknown recovery; a desired read-back or expired lease does not prove
   that an earlier uncertain request ended or make replay safe.
6. Complete source/ownership preflight, historical queue reconciliation, upgraded
   worker drain and fence-preserving rollback checks before activation. A green
   isolated test run or a gate-off code release is not activation approval.

See [delivery boundaries](../../docs/crm-relation-delivery-safety.md),
[fresh-import identity evidence](../../docs/crm-import-verified-identities.md),
[scalar and legacy transition safety](../../docs/crm-scoped-scalar-sync.md) and
[card history](crm-sync-history.md) for implemented layers and remaining limits.

## Verification

`test_tenant_crm_relation_contract.py` covers strict immutable intents and narrow
source-card semantics. `test_tenant_crm_relation_delivery_postgres.py` exercises
transactional storage and a deliberately stronger conditional mock.
`test_crm_relation_fences_postgres.py` exercises native PostgreSQL authorization
fences. Kommo planner tests preserve modeled secondary contact IDs; they do not
establish provider promotion, no-main or concurrency behavior. None of these
suites proves a production conditional relation adapter exists.

Official narrow API references: [link](https://developers.kommo.com/reference/linking-entities),
[unlink](https://developers.kommo.com/reference/unlinking-entities),
[observed links](https://developers.kommo.com/reference/linked-entities) and
[cardinality](https://developers.kommo.com/docs/subject-area).
