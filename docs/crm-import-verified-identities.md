# Verified identities from existing Kommo imports

## Bounded implementation

An already-configured Kommo contact/lead read can now retain server-only
provenance. The request still uses the existing URL, request count, payload,
refresh and retry behavior. The credential store exposes the selected provider
and connected_at from the same row snapshot as the token. No token or setting is
changed by this addition.

Only a direct successful GET of `contacts` or `leads`, with no redirect history
and an unambiguous bounded page of canonical numeric IDs, produces an in-memory
observation. The observation includes the actual successful attempt's tenant,
account domain, connection generation, resource, observed IDs and an opaque
request evidence ID. Refresh or a 401 retry uses the credentials of the final
successful attempt, not a queued job's assumed account. Plain dicts remain
compatible; JSON fields named `observation` are never accepted as evidence.

The page transaction locks the tenant and all relevant integration rows, checks
that this observation still matches the selected connection, then checks the
existing job/run/connection fences. It retains those locks through import,
verified mapping insertion and checkpoint commit. A stale response cannot be
attached to a new generation; a valid new-generation response cannot be saved
under an old job. A concurrent preferred OAuth INSERT is fenced even when only
a manual connection existed when the read started.

A verified mapping is registered only in the branch that just INSERTed the new
native contact or deal, after the existing import policy, required-field and
legacy-link checks. The mapping, native row, legacy link and page checkpoint share
one transaction. Uniqueness conflicts abort that transaction, rather than
reassigning an existing internal or external identity. Existing mapping evidence
is retained on replay. No provider operation, grant or global gate is activated.

## Deliberate non-adoption

These paths never create verified mappings for:

- A pre-existing native row found by a legacy provider ID.
- A contact selected by phone/email matching, even if the remote record exists.
- An existing imported row that lacks an observation, including rows imported
  before this change. Reading the same numeric ID again is not verification.
- A previously verified row after reconnect or account/auth-mode generation
  change. The old binding remains historical and cannot authorize the new one.
- Company, pipeline or stage identities, nested contact/company relationships,
  browser-supplied metadata or BEIN provider records.

The legacy import's existing matching and field-update behavior is unchanged.
Registering a freshly imported deal does not certify its existing pipeline,
stage, contact or company mappings. This is not a bulk backfill or a migration
that upgrades old `saas_crm_entity_links` rows.

## Audit of remaining hooks

`tenant_crm_sync_outbox._send_unfenced` receives a naked create result, stores the
legacy link in a later transaction and does not currently retain exact source
binding. No create/export hook was added here. It needs a separately verified
create receipt and current local-card/connection revalidation; ambiguous creates
must remain unknown, never repeated to obtain an ID.

`TenantKommo.request_bound` requires an already verified mapping and is not a
bootstrap path. A successful record GET alone cannot prove a historical native
record belongs to that remote record. The `owner_verified` storage primitive
exists, but no browser verification UI or permission flow is implemented.

BEIN's client has base URL/branch configuration, but no generation-bound identity
receipt contract or supported relation mapping schema. There is no BEIN import
path here and no safe BEIN registration hook. It remains unsupported.

For existing records, the product choice is an explicit owner assertion of the
exact native UUID, provider account/generation and remote entity/ID, with a
current-owner check, current connection fence and durable evidence. Any remote
read would confirm that exact asserted target, never choose one by name/phone or
coincident numeric ID. Decide that UI and evidence contract before implementing
it. Do not reuse automatic import as owner verification.

## Verification

Synthetic transport tests cover pre-send refresh, 401 retry, actual final source,
manual/OAuth distinction, redirects, malformed/duplicate IDs, JSON spoofing and
unchanged request count/body. Isolated SQL tests cover rollback with the page
checkpoint, current/old job generations, tenant/provider/entity/ID mismatch,
legacy and phone/email non-adoption, uniqueness collision and immutable evidence.

The separate localhost PostgreSQL fence suite covers concurrent reconnect,
preferred integration INSERT and duplicate imports. It requires an explicit
`BEINTASKBOT_TEST_POSTGRES_DSN` and rejects non-local hosts. PGlite/WASM tests do
not prove multi-session lock behavior. No production/provider calls or real
customer records are used by these tests.
