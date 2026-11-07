# Case creation without developer assignment

The shared dialogue/legacy-CRM Create action asks only to create the case. It does not load developer destinations, assign a developer, prepare a consultation, or dispatch a message. The source tenant, actor, route, and durable request UUID remain verified during response recovery. An uncertain browser request retries that same payload, never a new UUID.

Source support and dialogue permissions remain required. The employee assignee (`assigned_to`) is unchanged. The case detail no longer edits a developer as a responsible party; an existing value is shown only as historical information. The existing consultation panel remains the separate Ask developer action.

No schema or data migration is needed. Existing `assigned_developer_*` values, foreign keys, audits, and legacy PATCH compatibility remain. Older clients and saved browser requests may still submit a complete explicitly confirmed developer payload; the server validates and preserves that old contract without sending anything. Partial legacy payloads are rejected. New developer-free requests do not overwrite an existing case's historical assignment.

Automatic dispatch remains configured by the agent response policy and `handoff_ticket`, not by the removed case-creation picker. This change neither enables automatic dispatch nor changes recipients.

Regression coverage: `test_tenant_support_inbox.py`, `test_operator_case_submission_ui.cjs`, `test_operator_case_recovery_ui.cjs`, `test_operator_case_recovery_browser.cjs`, and `test_tenant_support_inbox_ui.cjs`. Tests use synthetic local data only.
