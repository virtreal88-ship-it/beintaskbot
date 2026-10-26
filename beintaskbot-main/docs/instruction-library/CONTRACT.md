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

## Reusable video pilot (simulation only)

A saved instruction card can hold several private MP4 assets in the existing
PostgreSQL storage (BYTEA, as for library screenshots). No bucket, credentials,
provider import, public media URL, paid processing, or CRM attachment is needed.
Uploads are decoded with local ffprobe and capped at 100 MiB / 120 seconds.
The current pilot does **not** import WhatsApp media, including message 18cee9ee.

Each asset has a SHA-256, immutable file bytes, version, task, product,
product version, and when-to-offer description. Uploads start unapproved. Only
an authenticated active company owner can approve the exact version/hash as
client-safe after reviewing visible data. Revocation disables discovery and
confirmation. Metadata edits reset approval and increment the asset version.
Sources remain private; no raw CRM URL, phone number or JID is returned.
Video approval does not change the instruction's text publication lifecycle.

The opt-in existing `instruction_library` expert tool exposes bounded approved
video references alongside text evidence. Selection is lexical task matching,
not embedding search or a general semantic classifier. The owner should use
short task terms matching customer vocabulary. Missing product/version or
multiple matches require clarification; nothing selects a random video.
The when-to-offer description is returned as guidance, not interpreted as an
executable rule. Normal text knowledge and operator attachments are unchanged.

Existing card/API demonstration:
1. Save/review/publish a synthetic instruction to a test agent.
2. Attach a locally generated MP4 to its card, with task, product, version and
   when-to-offer. The private preview requires current member authorization.
3. Owner reviews the file and selects “Approve reuse”.
4. In the same card's “Virtual offer test”, select the agent, enter a synthetic
   question, product/version, and the text answer first. “Find and offer” creates
   an exact asset offer. “Simulated client yes” creates a virtual receipt.

`video_offer` is bound to actor, tenant, sandbox dialog, agent, asset version/hash,
lesson version and a unique offer UUID. A newer offer supersedes the previous
outstanding offer in that dialog. Confirmation rechecks current source/agent,
asset integrity/approval and expiry (10 minutes). Dialog locking serializes
competing offers and receipt writes; a repeated confirmation returns the same
receipt. Unknown receipts block retries. Every receipt is labelled simulation,
`customer_sent=false`; sandbox consent is never represented as real client
consent. The API has **no live delivery branch**, even if production flags change.

Remaining pilot gate: connect authenticated customer consent and delivery to the
existing operator/Connect attachment transport, with its actual mode, scope,
stop/handoff and automatic-send pilot guards. This draft does not claim live
AI delivery, transport unknown-outcome handling, or a reviewed reusable copy of
any original customer video. The existing operator file route uses Kommo Drive and caps uploads at 5 MiB;
Connect AI currently exposes text delivery only. Its video upload/send contract
must be verified before adding an adapter. These limits were not changed.
Separate pilot approval/review is required before enabling that boundary. No customer-send permission is granted here.

### Bounded private storage

The operational byte budget is 500 MiB per company, across all instruction
videos including inactive/revoked history. It is independent of publication
permissions and knowledge/case counts. Upload transactions acquire a tenant
advisory lock before deduplication and the byte-budget check/insert. Repeating
identical bytes/metadata on a card returns its existing asset/version; changed
metadata must use the explicit metadata edit (which resets approval). The same
file on a different instruction consumes its actual stored bytes. Quota exhaustion
returns 413 without deleting or reactivating anything. This pilot has no purge
operation and never silently discards history to make room.

HTTP admission allows one upload and two preview streams per worker. Uploads
spool to a temporary file in 64 KiB chunks, with a 100 MiB bound; validation/hash
read bounded blocks. At the single final database bind there is at most one
100 MiB file buffer per worker (plus bounded driver encoding overhead), without
an accumulated bytearray/bytes duplicate. Temporary files close on all outcomes.
The mutation path has an additional process upload slot. The stored content/hash/
size are immutable at the database boundary. Metadata/approve/revoke/confirm
queries never select or return file bytes, and confirmation does not rehash the
blob. Byte accounting uses stored size metadata and includes every retained row.

Private playback authenticates before revealing size and supports a single HTTP
byte Range (206), suffix/open ranges and unsatisfiable/multiple ranges (416).
Both ranged and full GETs read/write at most 1 MiB at a time with network back
pressure; every block rechecks active member/tenant authority. Public share tokens
have no video access. The prior successful virtual receipt is replayed after
expiry/revocation under the same authenticated tenant/actor/dialog/agent binding,
without another send/audit event. New sends still fail closed.

This is a storage/selection/virtual-consent sandbox. Its manually entered text
answer and simulated yes are not an AI conversation or authenticated real-client
consent. Native independent-process byte-quota and deduplication races run only
in the existing disposable localhost PostgreSQL fixture, including genuine CI.
