# Owner-authorized employee default-deny transition

This is opt-in per company. Deploying the code does not reset any permissions.
The scoped-access engine (`TENANT_SCOPED_ACCESS_ENABLED=true`) is required.
After activation, disabling that engine causes platform connections to fail
closed rather than returning to legacy authorization. Keep the engine enabled.

The verified company owner can POST to `/api/platform/members` with same-origin
credentials and `expected_tenant_id` / `expected_user_id` matching that session:

1. `action: "preview_employee_default_deny"` returns counts and a digest.
2. `action: "apply_employee_default_deny"`, with `expected_digest` from the
   preview, applies exactly that snapshot. A changed snapshot returns conflict;
   preview again rather than guessing. The CLI in
   `scripts/employee_default_deny.py` offers the same preview/apply contract.

Effects:
- All non-owner members, including inactive members, lose legacy permission lists
  and legacy AI/native-CRM grant flags. Outstanding invitation grants are reset.
- Every missing matrix action becomes denied, including new members. Existing
  explicit matrix actions are preserved. Owner membership is not changed.
- WhatsApp numbers, source assignments, contact identities, notifications and
  other employee data are not changed.
- Profile access and the existing assigned-task read/complete exception remain.
- News and Instructions require explicit read access; public token links and
  publication authority are separate and unchanged.
- Owners can reopen matrix actions or the separate Linear/hot-orders controls.

The transaction records previous grants in a company audit event, fences current
readers and competing permission writes, and is idempotent. Repeating it does not
revoke rights granted by the owner after activation. Do not disable the persisted
policy through generic workflow settings or restore a pre-feature application
version after activation without a separate reviewed access migration.
