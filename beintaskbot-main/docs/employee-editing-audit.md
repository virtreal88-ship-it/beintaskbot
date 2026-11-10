# Employee editing audit

Baseline: main `b4b25425`. Scope: SaaS `/app`, not the legacy single-company
WebApp. No production accounts or permissions were changed during the audit.

## Confirmed gaps

| Area | Current behavior | Required correction |
| --- | --- | --- |
| First name, last name, phone, login, avatar | Always read-only in existing employee cards, even for a company-created account | Let the canonical company owner edit company-managed accounts. Update account and profile stores together. |
| Email and password | Existing UI says only the employee can change them | Separate `fix/owner-password-direct` work supplies direct owner assignment. Do not duplicate that implementation. |
| Role | Card offers only worker and manager, and labels worker as operator | Offer the existing supported roles with distinct labels; administrator assignment remains an owner action. Do not add ownership transfer here. |
| Permissions | Card exposes five base grants; other grants are retained and described as compatible/legacy | Owner needs a complete and coherent permissions view. Integration-specific scope stays in integration settings as requested; module switches alone must not claim effective access. |
| Employee workflow | Task approval, AI-use, hot-order eligibility and notification settings live in a second workflow form | Surface a clear entry to the same authoritative settings rather than duplicate their data or policy. |
| Company access | Owner already can activate/deactivate another membership | Keep this separate from the global account status. A company-specific switch must not disable another company's account. |
| WhatsApp identity | Owner can edit membership-local numbers, but inactive employees cannot edit them | Explain the inactive state and allow reactivation first. Do not confuse contact phone with WhatsApp recognition. |
| Current account | Owner cannot deactivate/demote own owner membership in this form | Keep ownership transfer and account lifecycle separate from ordinary employee editing. |

## Data model boundary

`saas_tenant_members` stores the local display name, role, grants and access.
`saas_auth_users` and `saas_user_profiles` store the login identity shared by all
memberships. Company-created, exclusive accounts can be managed by their owner;
an employee with membership in another company is not a company-local identity.
This does not justify making every identity field read-only for every employee.

## Patch prepared here

`tenant_member_profile_assign.assign_member_profile` accepts a sparse profile
patch and uses the same company-ownership check as email/password assignment.
It updates both profile stores, keeps the company alias unchanged, validates
login uniqueness, advances the card version, and records changed field names.
Changing the login revokes the employee's old sessions and password-setting
links; a phone/name/avatar change does not unnecessarily sign the employee out.

This module is integrated with the direct-password work through the owner-only
API action and server-provided profile capability. The existing Save action
saves changed profile fields and then any membership edits using the returned
card version. The existing supported role choices are now shown distinctly.
The credentials/profile correction shipped in PR #584. The next patch below
consolidates the remaining company-local employee settings.

## Acceptance

- Owner edits an eligible employee's profile and sees the same saved data after
  reopening the card.
- Unchanged fields and company alias remain unchanged.
- Duplicate login or stale card fails atomically with a field-level explanation.
- Another company, a shared identity, or a non-owner cannot edit the account.
- No actual employee credentials, messages, rewards or integration settings are
  changed by the tests.

## Unified employee settings (after PR #584)

Baseline: main `07e8416f`. The employee card now exposes owner-controlled module
switches, AI use, task/deal/order approval overrides, order service eligibility,
and notification channel preferences. Optional work settings remain collapsed
until needed. Each override supports inheriting the company rule. Account
credentials, integration scope and company membership remain separate concepts.

Updates are sparse and target one membership. They preserve other employees,
inactive employees, unknown legacy grants, provider bindings and Linear/risk
notification preferences. Explicitly toggling a module off also removes its
older action-level grants; untouched scoped grants are not broadened. A separate
settings version prevents an old card overwriting newer workflow preferences.
The existing tenant policy continues to decide effective access by role and
company modules. This patch does not grant any real employee new permissions.

The workflow screen no longer duplicates role, grants or work settings. Its
remaining employee section changes manager pipeline scope only, with an exact
prior-scope check and a sparse merge. The browser API rejects an obsolete bulk
employee form instead of silently replacing newer employee settings. Programmatic
legacy workflow calls retain their compatibility contract.

Remaining structural boundary: pipeline scope is still in the existing workflow
screen, not yet moved into a unified multi-provider integration editor. Linear
page access and notification preferences remain editable in the existing Linear
integration panel, independently for each employee. Observer behavior is unchanged. Company owners
cannot take over a shared global identity, transfer ownership, or turn a
company-only switch into global account deletion through this employee form.

Tests use disposable local PostgreSQL and synthetic accounts only. New tests
cover sparse settings, stale versions, concurrent scope/settings writes,
foreign-tenant isolation, partial grants, retained services, and RU/AZ/EN UI.
The real cookie/Chromium CRUD scenario additionally saves and reopens module
permissions and work preferences at 360/390/768/1440 px. Its local browser launch
is blocked by the execution environment's Unix socket restriction. The first
remote CI run passed all 254 tests including these scenarios; the final copy is
checked again in CI before release.

## Independent pages and actions

Employee cards now separate identity, permissions, and personal settings into
three tabs. The personal settings tab controls page visibility without changing
operation rights. Profile remains available; the list includes the standalone
Instructions link and dynamically mounted navigation. Page preferences persist
per company membership, with an independent version and sparse patch.

The native CRM exposes separate view/create/update/delete controls for tasks,
deals and contacts (companies share contact rights). Native action overrides
are stored separately from legacy module grants so changing one native action
cannot disable Kommo or expand its provider scope. Other existing product
modules retain explicitly labelled all-actions access switches; this release
does not pretend that every older API has four independent CRUD operations.
Provider scopes, including Linear and Kommo pipelines, remain separate.

New page capabilities fix the specific mismatch where granular native view
permission allowed the API but the sidebar recognized only a broad legacy
module grant. Visibility alone cannot supply a missing view/prerequisite.
Hiding a page is a navigation preference, not a substitute for API authorization.
No real employee's rights are changed automatically by this release.

During this work, native deal and company collection action dispatch was also
corrected: an item-only archive/link request cannot fall through to creation
using delete/assign authority. Regression tests cover these permission/action
mismatches independently of UI controls.
