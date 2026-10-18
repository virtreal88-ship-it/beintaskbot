# Instruction library integration

Base production main: b109ebe1. Library/editor c5a51dd2; latest Sol player
304a9aa9 (including e7523974 compact captions). One shared player file.
Conflicts: retained both local dependency ignores; retained Sol player with
library generic 1-32 slides and RU/AZ/EN. Explicit default 1.75x in runtime,
select control and tests. Rebuilt offline preview from that same source.
Sticky controls now remain above transformed slide layers inside dialogs.

## Internal expert contract

New opt-in read-only tool `instruction_library`, available in the existing AI
Teams tool settings. Effective primary/expert intersection still applies.
No agent setting is changed by deployment. The lesson must be published to the
calling primary's Knowledge Sources. Tenant, primary role, active source,
employee approval, published lesson and source/snapshot agreement are required.
The search runs before any prospective browser fallback. It returns a bounded
internal excerpt, opaque lesson/source IDs and an explicit UI-compatibility caveat.
No public link, arbitrary URL, transport action or customer-send permission is
created. Not-found returns integration_unavailable for the browser fallback.
No isolated live browser adapter exists: this release does NOT enable autonomous
browser instruction generation. It does not claim unknown UI versions are verified.
Existing expert timeout, accounting, Human Mode/group fences and settle checks
remain in place. Manual/Automatic and coordination are unchanged.

## Schema / rollout

Two additive tables: saas_instruction_lessons (draft/published JSON, version,
share hash) and saas_instruction_assets (sanitized JPEG bytes), plus a unique
partial index on public_hash. No ALTER/DROP/backfill of existing business data.
Pillow >=11,<13 is declared. Local isolated staging used PostgreSQL/WASM and
Pillow 12.3.0; image decode/re-encode and metadata removal exercised.
Railway staging/prod permissions and the deployed Pillow image are NOT verified;
no remote deployment was requested or performed. Verify these before release.
Rollback application without dropping tables; retain lesson/source data and
review public-link exposure separately. Published knowledge uses the existing
Knowledge Sources and remains active unless explicitly revoked/edited.

## Targeted checks

92 Python tests passed: library lifecycle, sources, private/public/revocable links,
HTTP identity/origin, tenant/role isolation, Pillow, expert lesson lookup,
Human/Pause, accounting and group coordination integration, routes.
Four Node/JSDOM suites passed: editor, generic RU/AZ/EN player, full Sol timeline,
AI Teams UI. Real Chrome: local library, edit, submit, employee approval, publish
to synthetic agent; preview desktop and 390px mobile frame. No customer sends.
No /webapp, Connect, WhatsApp session or live agent configuration changes.

Real-clock Chrome regression: PASS on repeat uninterrupted run, all eight slides
at 1.75x, no blank frames/upscaling, compact captions clear of focus rectangles.
Initial full replay was interrupted by a pause and timed out; no product bypass
was introduced. Desktop/mobile control and pause behavior remain intact.
