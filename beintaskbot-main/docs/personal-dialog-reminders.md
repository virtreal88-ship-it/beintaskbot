# Private dialogue reminders

In /app → Dialogues, use “My reminders” → “New reminder” without linking any chat.
The menu for a Connect conversation also contains “Remind myself” to link that
chat. Kommo is excluded from chat linking until its exact route can be verified.
Enter a short note and a date and time shown in the device’s time zone.
“My reminders” shows upcoming and overdue items in the dialogue list.
While the page is visible, the count refreshes about every 30 seconds.
Reminders survive reloading, but are not delivered while the app is closed.
They never send a WhatsApp message, push notification, or ERP / Operator request.

Only the active authenticated creator, in the original company, can list,
complete, or open a reminder. No dialog identifiers are included in the list.
The optional open action checks the current source and dialogue access again;
the ordinary dialogue view then performs its own history check.

## BeinSystems pilot rollout

Stock boot and stock traffic do not touch the reminder table. To pilot only
the exact `BEINSYSTEMS_OPERATOR_TENANT_ID`, leave the old global
`TENANT_PERSONAL_DIALOG_REMINDERS_ENABLED` switch unset. It still works for
existing explicitly global installations; do not use it for this pilot.

1. Release the code with the pilot and provisioning switches unset. Verify
   the workspace mapping and current recovery/restore evidence before any live
   DDL. This rollout must not buy backup capacity, export data, or run a live
   restore. If recovery cannot be verified, do not provision.
2. Use the already deployed schema if it is verified. If it is absent, with
   the UI still off, set `TENANT_PERSONAL_DIALOG_REMINDERS_PROVISION_ON_STARTUP=1`
   for one approved Worker startup. Only the private reminder table and
   pending index can be created. Under the existing startup advisory lock and
   a fresh bounded transaction, an existing exact schema is verified without
   DDL or row changes. Wrong or missing parts of existing schema are an error,
   not repaired. Errors roll back any newly created objects without blocking
   ordinary Worker boot.
3. Verify the exact production Worker and its static log signal
   `Personal reminders schema preflight: ready`. An `unverified` or
   `workspace_not_configured` signal (or no signal) is not approval to enable.
   After `ready`, remove the provisioning flag. Set
   `TENANT_PERSONAL_DIALOG_REMINDERS_BEINSYSTEMS_ENABLED=1` only when
   ready for the exact workspace. Test in /app → Dialogues → My reminders
   without opening a customer chat; verify no entry is exposed in another
   workspace. The same profile, current-dialogue permission, actor and source
   protections still apply.
4. Rollback: unset the pilot and provisioning switches. Keep the schema and
   rows intact. No background delivery, role changes, or operator settings are
   necessary.

The HTTP endpoint never provisions schema. The pilot also verifies the
canonical shape before any reminder data read or write. If anything is missing
or incompatible, the API fails closed and the control stays hidden. The separate manual preflight
function `tenant_personal_dialog_reminders.migrate(cursor)` remains available
only to existing explicitly approved migration operators.

## Shared reminders

An operator can schedule one reminder per exact dialogue from its action menu.
The shared list shows pending reminders and the badge appears when a reminder
is due. An accepted dialogue shows its reminder to its current operator. A
closed or dismissed dialogue shows it to Online operators with current view
access to that source. A waiting dialogue has no assigned operator.

An operator with ordinary dialogue access can clear the badge by opening the
dialogue. Other eligible operators can open the shared reminder card. It
shows the shared note and schedule while conversation history remains subject
to the dialogue permissions. The first authorized open clears the marker
for everyone in the database. Other tabs reflect this on their next refresh.
Background history reads never clear reminders.

Shared reminders use the same source IDs as the four-state status routing
rollout. Deploy with both shared-reminder switches off. Enable
`TENANT_SHARED_DIALOG_REMINDERS_PROVISION_ON_STARTUP=1` for the optional
schema startup. Verify the `Shared reminders schema preflight: ready`
signal before disabling provisioning and enabling
`TENANT_SHARED_DIALOG_REMINDERS_ENABLED=1`. The separate personal reminders
switch continues to control the private feature.
