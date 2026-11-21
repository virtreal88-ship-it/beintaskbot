# Optional employee work status rollout

In /app, all active staff can choose their own Work status in Profile, even
if they cannot open Dialogues. The existing Dialogues > More > Status control
uses the same status for operators with update access. Choices: Online, Away,
Lunch and Offline. Before any explicit choice, Online is effective once the
service and schema are ready; reading never seeds a row or creates an audit
event. A manual choice persists until the employee explicitly changes it,
across tabs/sessions, with no heartbeat or timeout.

The current verified company owner sees a small work-status badge by active
employees' names in Employees, in addition to the separate account-active
badge. It enriches only rows the existing employee store authorizes and does
not expose work status to managers/viewers or change card versions. No role,
route, admission, transfer, ownership, alert or off-work rule is changed
by this rollout. The existing legacy admission helper is untouched.

## Independent controls

- `DIALOG_EMPLOYEE_STATUS_PROVISION_ON_STARTUP=true` prepares a shared table
  without enabling these surfaces. It is off by default.
- `TENANT_DIALOG_EMPLOYEE_STATUS_ENABLED=true` enables standalone status for
  active company members only after the four-state schema verifies. Off by
  default. Existing compatible BeinSystems Dialogues status still works with
  its prior three options if this new feature is off.
- Leave `TENANT_DIALOG_PRESENCE_ROUTING_ENABLED` as it was. Do not turn it on
  for this release. If the separate legacy pilot is already active, its old
  recorded-only rules still apply, and the shortcut explains when Online
  must be explicitly confirmed in that legacy mode.

## Prepare, verify, enable, roll back

1. Deploy the code with both new switches off. Do not set any employee
   status and do not backfill.
2. In an approved prepare deployment, enable only startup provisioning.
   It runs last in an optional transaction under the existing startup
   session lock (2-second lock / 5-second transaction limit). The function
   installs an empty table if missing, or expands only the verified compatible
   three-value constraint in place; no row is rewritten, created or removed.
   It refuses to create the table if the legacy routing switch is already on,
   because that table could activate old admission code as a side effect.
3. Check exact Worker deploy success and log outcome
   `Employee status schema preflight: created` or `ready` as evidence of
   a committed four-state table. `unverified` leaves the app running with
   standalone status inaccessible; stop and investigate (do not repair drift
   on a running request). An existing incompatible shape is never altered.
4. Turn off one-time provisioning. Only after approval enable the runtime
   switch; verify on synthetic memberships using signed sessions, not real
   employees. A worker without Dialogs can set their own status; an owner
   sees authorized active rows only; old module rights stay unchanged.
5. Roll back by turning off the two new switches on the same code. Keep the
   additive schema and choices. The older three-option BeinSystems shortcut
   treats retained Offline as Away while the feature is off (other explicit
   choices still work); re-enabling restores Offline unless changed. Do not
   downgrade code after enabling Offline if preserving older shortcut UX
   matters: pre-release clients do not recognize the fourth enum. They
   still treat it as not Online in the independent legacy routing predicate.

## Consumer contract

`is_effectively_online(..., lock=True)` requires enabled/ready status, current
active membership and Dialogs view before interpreting a missing row as
Online. A stored Away, Lunch or Offline is not Online and never expires.
Consumers independently verify the required current role and precise route
access. `is_recorded_online` still requires an explicit Online and current
Dialogs update. Do not use the legacy routing helper `is_online` to infer
work status. The role `saas_tenant_members.role='operator'` will be used
only in a separately approved reception/routing slice.

## Online operator routing

The routing switch is independent of the four-state profile control. With both
switches enabled, eligible operators need current dialogue view and update
permissions plus access to the exact source. An operator with no saved choice
defaults to Online. Away, Lunch and Offline remove the operator from the
waiting queue and from transfer targets. Existing accepted dialogue access
continues until a verified newer incoming message returns an unavailable
operator's dialogue to the waiting queue. The earlier episode is kept.

Before enabling routing, check Online operator coverage on the active sources.
Turning routing off restores the previous eligibility rules without changing
stored statuses or moving conversations.
