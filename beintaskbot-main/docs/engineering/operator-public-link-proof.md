# Operator public-link permission proof

This owner-only check is independent of active Operator integration, the
accounts-only grant, CRM mappings and dialogue writes. It does not activate or
promote a saved credential, and it does not enable the public-link icon.

Before any production use, confirm the expected operator identifier exactly as
returned by `/user/me` and confirm one approved disposable Operator account.
Pin both in private configuration, respectively
`BEINSYSTEMS_OPERATOR_PUBLIC_LINK_PROBE_IDENTIFIER` and
`BEINSYSTEMS_OPERATOR_PUBLIC_LINK_PROBE_ACCOUNT`. Do not use a customer's
account, store these values in the repository, or put provider credentials in
either setting. With either unset or malformed, the UI is unavailable and no
request is sent.

Only a confirmed click on the owner settings card invokes the test. It checks
the exact saved candidate revision and identity using the existing fenced
`/user/me` path. It then reads one existing link for the configured demo account with
`GET /task/getPublicLink?account=...`. The provider only exposes links
issued from version 1.0.88 onward through this method. A missing or older
link does not trigger issuance. There are no automatic retries. The public-link value and credential are never
returned or logged; status and candidate revision are the entire UI result.
A changed candidate or active connection makes even a successful upstream
result ineligible to display. Any previously configured read-only accounts
capability remains unchanged.

A passing proof is not a production grant. If approved separately, design a
distinct purpose-bound grant with owner identity, verified pending revision,
identity fingerprint, expiry, and mapping provenance; preserve existing
authorization and integration precedence. Do not reinterpret accounts-only
authority, infer active entitlement from this test, or promote the token
without separate approval.
