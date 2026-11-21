# BeinSystems status-only presence pilot

The existing Dialogues menu (`…` → employee status) shows the authenticated
employee’s own Online / Lunch / Away status only for the configured BeinSystems
workspace. The endpoint rechecks the existing dialogue permissions and never
sets another employee’s status. No status is seeded; each employee chooses.

If the table is missing, the existing explicit `tenant_dialog_presence.migrate`
can be run through an approved bounded preflight. When that path is not available,
a reviewed Worker startup can opt in with:

- `DIALOG_PRESENCE_STATUS_PROVISION_WORKSPACE_ID`: exact canonical UUID matching
  the configured active BeinSystems workspace.
- `TENANT_DIALOG_PRESENCE_ROUTING_ENABLED`: keep unset or false. The dedicated
  startup rejects provisioning when presence routing is requested.

The otherwise unchanged startup does not probe or provision presence. The opt-in
uses the existing startup advisory lock with 2-second lock and 5-second
transaction limits, after mandatory schema work has committed. It requires the
dialogue authorization schema to exist and does not install it. It validates
the exact table/index/constraints and rolls back the optional transaction on
a mismatch. Compatible existing tables are verified, not changed. The migration
never inserts employee statuses or modifies scripts, source ACLs, inbox
admission, transfer, or any other company’s controls. Remove the opt-in setting
after verified provisioning; status visibility uses the existing workspace and
permission gate, not the provisioning setting.
