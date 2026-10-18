# Instructions library and editor

Branch: codex/instruction-library-editor, based on origin/main b109ebe1.
All local/remote refs were fetched and checked before implementation; no prior
library/editor implementation was found. No merge, push or deployment performed.

## Reuse and scope

/app/instructions is linked from /app. It uses the Sol player source from
codex/beinsystems-instructor / 304a9aa9, including compact collision-aware captions,
focus, crossfade, seek, rates and reduced motion. Release default is 1.25x, explicitly selected for this release. No second player implementation was added.
The same player accepts 1–32 slides and localizes controls RU/AZ/EN. Cue structure,
image dimensions/hash, areas and playback remain compatible with the eight-step
pilot. The integration adds opt-in instruction_library evidence to call_expert;
the existing pilot lookup still has its stricter eight-step/operation validation.

Two PostgreSQL authoring tables store drafts and cleaned screenshot bytes. They
are not searchable AI knowledge. Publish uses save_knowledge(connection=conn),
the existing source/chunk storage, per selected agent; provenance records approval
and lesson operation/language/UI version. No separate retrieval/index/provider.
Editing a published lesson deactivates its previous sources and revokes its public
link; a fresh employee review is required. Inactive source versions remain for audit.

## API

Every private GET supplies expected_tenant_id + expected_user_id from /me; POST
includes them in JSON and requires canonical Origin. Live existing AI-management
authorization applies (owner or admin with ai_agents_manage); inactive membership
and archived/suspended company are denied. No new employee permission is inferred.

- GET /api/platform/instructions?q=&state=&offset=: 50 rows plus next_offset.
- GET /api/platform/instructions/{id}: document, version, server-owned state.
- POST collection: action=upload + base64 image; returns asset_id,width,height,sha256.
- POST /{id}: action=save, doc, expected_version (0 for a client UUID on create).
- POST /{id}: action=submit|approve|publish|share|revoke + expected_version.
- approve requires review_confirmed=true; publish requires 1–8 same-tenant agent_ids.
- share requires already published state; UI additionally requires explicit consent.
  Returned random bearer token is stored only as SHA-256 and rotated on each share.
- GET /{id}/assets/{asset_id}: live authorized member and image referenced by lesson.
- GET /api/instructions/shared/{token}[/assets/{asset_id}]: published snapshot only,
  company must be active, token must match; no draft/unreferenced-image access.
- Private link: /app/instructions?lesson={id}; public: ?share={token}.

Document schema_version=1, id, title, operation, language=ru|az|en, slides and
subtitles.slides. Slides retain id/order/title/explanation/note/image/areas/playback.
Image asset_id is server-issued UUID.jpg. Focus coordinates use original pixels.
All cue starts, durations, bounds, asset ownership/hash and maximum sizes are
server-validated. Client review_status is ignored. Server state is authoritative.

The UI edits title/text/cautions, images, order, duration, a drawn focus region and
numeric coordinates. Additional multi-region/keyframe JSON is under Advanced.
Subtitles use editable ms | text cue lines. Preview requires saving first.
Unsaved edits trigger exit warnings and tenant/user-scoped session recovery;
expected_version prevents silent overwrites. No concurrent collaborative editing.

## Limits / integration

32 slides, 4–30s each, bounded text/cues; 200 lessons / 256 uploaded images per
company; UI upload <=600 KB (server raw <=2 MB), JPEG/PNG <=4096px. Pillow decodes
and re-encodes JPEG without EXIF/comments; staff must still review visible personal
data before publishing. No automatic translation, OCR, AI generation or bulk import.

Only authoring rows/assets are new. Additive schema uses existing initialization;
check staging schema permissions and Pillow installation before integration.
Reconcile tenant-app navigation and route/asset catalogue with concurrent branches.
Keep one authoritative Sol player during integration; port generic slide limits
and RU/AZ/EN controls upstream, rather than keeping divergent copies.
No automatic customer delivery, AI runtime/settings, Connect, WhatsApp or /webapp changes.

## Verification

23 targeted Python tests pass: real isolated PostgreSQL/WASM lifecycle, publication,
version conflicts, tenant/role/archive isolation, source deactivation, public token
revocation, asset membership, cleaned image/dedup, HTTP identity/origin, route/assets.
Node/JSDOM editor tests pass: list/edit/save/review/preview binding, escaped text,
expected identity/version, unsaved guard and company reset.
Browser on loopback + isolated database: library/editor, eight-step Sol preview,
submit/approve/publish to a synthetic test agent, public published viewer.
Mobile 390px has no horizontal overflow after min-width correction.
No production APIs or paid services used. Test server is tests/run_instruction_demo.py;
it requires explicit --sample path and never reads production credentials.
