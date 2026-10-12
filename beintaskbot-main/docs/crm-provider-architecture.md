# CRM provider architecture

## Result

CRM Smart Assistant supports a secure tenant-owned BEIN ERP connection as a
second CRM provider. Tenant roles, permissions, workflow policy, notifications,
encrypted credentials and the provider transport are ready. Kommo-specific
snapshot identifiers and chat delivery remain isolated until the generic data
identity migration described below is complete.

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

## Remaining data-plane migration

1. Replace `kommo_lead_id` and `kommo_task_id` as domain identities with
   `(provider, external_id text)` while retaining compatibility columns during
   migration.
2. Move Kommo HAL parsing (`_embedded`, numeric IDs, Unix timestamps) behind a
   Kommo adapter; add a BEIN ERP adapter for UUIDs and envelope pagination.
3. Route deals, tasks, pipelines, stages and customers through the selected
   provider. Keep Kommo Talk/chat features capability-gated.
4. Add webhooks or bounded incremental polling for BEIN ERP once the event
   contract is available.
5. Backfill one tenant at a time and prohibit two providers from writing the
   same logical deal unless explicit mapping exists.

`tenant_bein_crm.py` provides the safe integration-token transport and resource
mapping. An owner activates it from the integration marketplace with the
production API host, integration token and branch.
## BeinSystems ERP connection

The application now has a tenant-owned BeinSystems connector in the integration
marketplace. The company owner supplies the production API host, branch UUID and
an integration token. The server verifies the token against the pipeline catalog
before it stores anything. The token is encrypted at rest, bound to the tenant,
never returned to the browser and removed when the integration is disconnected.

Supported by the public BeinSystems API adapter: deals, tasks, customers,
pipelines and stages. Chats, message history and notes remain capability-gated
because the published API does not expose dedicated endpoints for them. Kommo is
therefore not disconnected when BeinSystems is added.
