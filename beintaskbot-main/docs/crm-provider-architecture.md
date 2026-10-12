# CRM provider architecture

## Result

CRM Smart Assistant can support BEIN ERP as a second CRM, but the existing
runtime is only partially provider-neutral. Tenant roles, permissions,
workflow policy, notifications and the encrypted integration table are ready.
Synchronization, cached identifiers and chat delivery still contain Kommo wire
formats and must not be reused for UUID-based providers without an adapter.

## Confirmed BEIN ERP mapping

Official source: <https://docs.beinsystems.az/docs/api/>

- deals: `crm/leads`;
- tasks: `crm/tasks`;
- pipelines: `crm/pipelines`;
- stages: `crm/stages`;
- customers: `partners`;
- sources: `crm/sources`;
- lost reasons: `crm/lost-reasons`.

The API supports Bearer integration tokens (`bei_int_...`), branch scoping,
pagination, CRUD permissions and idempotent mutations. A stable
`Idempotency-Key` is mandatory for integration-token writes.

The published API catalogue does not currently expose dedicated CRM notes,
chat history or chat-send endpoints. Those capabilities remain disabled until
the provider publishes a contract or a separate approved storage strategy is
chosen.

## Required migration before activation

1. Add a tenant `primary_crm_provider` selection and encrypted BEIN ERP
   credential setup.
2. Replace `kommo_lead_id` and `kommo_task_id` as domain identities with
   `(provider, external_id text)` while retaining compatibility columns during
   migration.
3. Move Kommo HAL parsing (`_embedded`, numeric IDs, Unix timestamps) behind a
   Kommo adapter; add a BEIN ERP adapter for UUIDs and envelope pagination.
4. Route deals, tasks, pipelines, stages and customers through the selected
   provider. Keep Kommo Talk/chat features capability-gated.
5. Add webhooks or bounded incremental polling for BEIN ERP once the event
   contract is available.
6. Backfill one tenant at a time and prohibit two providers from writing the
   same logical deal unless explicit mapping exists.

`tenant_bein_crm.py` now provides the safe integration-token transport and
resource mapping. It is intentionally not activated until a production API
host, integration token and branch are configured for a tenant.
