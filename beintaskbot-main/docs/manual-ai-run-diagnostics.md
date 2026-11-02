# Exact-dialog manual AI metadata

The existing authenticated GET `/api/platform/ai/state/technical` accepts an exact `channel_id` UUID and positive `lead_id`, plus the usual exact `expected_tenant_id` and `expected_user_id`. Optional `request_id` filters one UUID; `limit` defaults to 10 and accepts only 1–10. Supplying incomplete/invalid exact-dialog parameters fails closed. Ordinary state/technical reads without these parameters retain their behavior.

This mode revalidates active tenant membership, the DB `owner` role **and** `saas_tenants.owner_telegram_id`, existing dialog permissions, route membership and current visible active channel. It does not require AI settings/credentials to be enabled to inspect historical metadata. Admins and additional members labelled owner cannot read this mode.

The result is `{coverage:"manual_runs_metadata_only", available, items, limited, limitations}`. Items contain request UUID, stored status and times, bounded safe source UUID/version hashes, and stored media-failure status/code/stage. `dispatch_proof:null` explicitly means no dispatch proof is present in this projection. No provider attempt, billing event, missing file or original cause is inferred from `unknown` or an absent failure reason.

No transcript, draft, customer content, JID, attachment URL, credentials or raw provider error is returned. The query projects only metadata from existing `saas_chat_ai_runs`; it performs no schema setup, writes, media downloads, provider calls or retries. Existing unknown attempts and financial holds are untouched. At most ten rows and twenty safe source references per row are returned. `limited:true` reports that older rows were omitted; optional request UUID can locate an older known operation without broadening the dialog scope.

A signed-in owner can open the ordinary JSON URL directly in the existing browser tab: `/api/platform/ai/state/technical?channel_id=<channel UUID>&lead_id=<lead>&expected_tenant_id=<current tenant UUID>&expected_user_id=<current Telegram numeric ID>`. The registered route uses the normal browser session cookie; custom headers or arbitrary JavaScript fetch are unnecessary. This URL contains no conversation JID or content. It does not initiate AI work.

## Optional single-source lower receipt probe

Add `include_source_receipts=1` **only with one `request_id`**. No client source, hash or index is accepted. The server selects the run's single stored `media_failure.source_id/source_hash/kind`, validates its safe UUID/hash format and verifies that the actual CRM source belongs to the exact authorized physical tenant/channel/lead and kind. Historical hashes are allowed; this is not a comparison against the current attachment version.

The matching item's `source_receipts` contains `status`, `reason`, table-presence booleans and at most one `flight` plus one `receipt`. `status:"unknown"` with reason `source_unverified`, `records_absent` or `records_unavailable` explicitly preserves uncertainty. `status:"records_present"` means metadata rows were found, not that dispatch happened. Missing selected runs still return `items:[]` without querying lower stores. Ordinary responses omit this block unless requested.

Both lookups use the complete stored `(tenant_id, channel_id, external_id, source_hash)` key. Only fixed-enum status/code/stage/safe failure code, timestamps, boolean `coverage_complete` and allowlisted processor are exposed. Safe stages include `video_decode` and `visual_input_prepare`. Missing tables are checked with `to_regclass`; no schema setup or mutation occurs. No observations, transcript, raw JSON, URLs, tokens, models or finance joins are returned.

Limitations are explicit: `source_records_not_request_linked` and `absence_not_dispatch_proof`. These shared source-keyed records may predate or postdate the selected run and can have been updated later. They cannot by themselves attribute a cause to that operation. A missing record ends this historical probe as unknown; it never triggers a retry, provider call or alternate broad search. Existing unknown states and holds remain unchanged.
