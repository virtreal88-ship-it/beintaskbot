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

## Preflight

This is disabled unless `TENANT_PERSONAL_DIALOG_REMINDERS_ENABLED=1`.
Provision the private table and index explicitly with
`tenant_personal_dialog_reminders.migrate(cursor)` in the approved DB preflight,
after validating on disposable PostgreSQL. Neither the request path nor server
startup performs DDL. The endpoint fails closed if the schema is missing.
Remove the flag to disable without deleting anyone’s reminders.
