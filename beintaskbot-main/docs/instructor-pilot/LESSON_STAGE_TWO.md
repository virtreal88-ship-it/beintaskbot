# Interactive instructor lesson, stage 2

Branch codex/beinsystems-instructor, previous pilot 642eb0d7 / 02af430f.
Reviewed current origin/main b109ebe1 without merging: expert result remains the strict
seven-field contract, Knowledge Sources still has content/provenance/agent_id.
No existing runtime, webapp, agent configuration or company data is modified.

## Working output

output/lessons/beinsystems-warehouse-transfer-az.html is a self-contained offline demo
review player with eight real captures embedded as JPEG blobs. Open the file in a browser;
press Başlat. It does not require login, ERP access, network assets, AI calls or a server.
This downloadable demo artifact is not a multi-tenant production endpoint. Do not publish
private company lessons using static public hosting. CSP denies network connections.

Single source: warehouse-transfer.lesson.json, schema_version 1. It includes operation,
language, review status, limited verification, and eight ordered slides. Each slide contains
title/explanation/note, asset filename/dimensions/SHA256, pixel-space important areas,
playback duration/transition/focus keyframes and return-to-overview flag. Capture hashes
match the previously inspected demo evidence. No new ERP navigation or paid analysis.
PDF remains an additional export: build_visual_guide.py now reads the same lesson JSON.

Player: docs/tenant-instructor-lesson.js + .css. mount(root,lesson,{resolveAsset}) accepts
an authorized same-origin/blob asset resolver; never arbitrary external image URLs.
Text uses textContent. Validation rejects paths, unexpected dimensions/areas and long shows.
At most eight slides, 144 seconds, 4096-pixel image bounds. No transport/publication hooks.

Autoplay begins on Başlat. Focus at 1.4s; smooth 650ms transform; return to overview before
advancing. Steps 1/2 focus only when fitted scale <0.6; desktop overview stays still.
Later small controls get explicit focus. Scale never exceeds one original image pixel per
CSS pixel; source images are not resampled/upscaled. On narrow screens this brings tiny
controls back to native detail, rather than requiring a click. Reduced-motion removes
animation. Manual pointer/wheel/keyboard interaction pauses; next/back pause too. Resume
keeps elapsed slide time. Hidden tab pauses. Last slide stops, no loop. Resize recomputes
bounds; mobile controls remain fixed above the bottom edge. Missing images fail closed.

The existing captures are 855x656 (last 870x668). They are sufficient for native-size
control detail but cannot support arbitrary 4K magnification; new high-resolution captures
would require another separately authorized demo inspection. Negative stock is not called
an error: lesson step 5 explicitly says company policy and save behavior are unverified.

## Employee review and Knowledge Sources contract

This pilot remains pending_review / form_preparation_only. Save/post/stock effects and
negative-stock policy are unverified. A reviewer may approve the form-preparation lesson,
never relabel it as a tested completed warehouse transfer. No approval is performed here.

Future publication through the existing employee approval path writes one existing Knowledge
Source (agent-scoped, active only after approve), content = serialized validated lesson.
Required provenance: review_status=approved, approved_by=employee identity, lesson={operation,
language,ui_version}, original source question IDs and private screenshot artifact IDs.
Do not create artificial chat messages or a second knowledge store. Source content may need
human-readable chunks plus the structured attachment in provenance when connecting standard
RAG indexing; the current pilot does not insert anything into production storage.

Server helper tenant_ai_lesson_contract.approved_lesson(cur,tenant,agent,language,ui_version)
performs exact operation/language/build lookup in existing Knowledge Sources and requires
active company, exact tenant+agent, active source and explicit employee approval provenance.
Caller must first run existing live employee/agent permission checks; it is not a public API.
Unknown UI version deliberately returns no automatic reuse. A verified build identifier and
review are required; the pilot's unreported UI version does not manufacture a cache hit.
On a miss return review_required, NOT an automatic browser/model call.

Proposed call_expert attachment from internal_attachment():
{type:instruction_lesson,schema_version:1,source_id,lesson_id,operation,language,verification,
 delivery:internal_only,customer_send_allowed:false,changes_dialog_state:false}.
Only server-owned approved source lookup may supply this payload. Recheck _live, internal
principal binding, budget, Human/Pause and group_revision before/after work; retain _settle.
Do not accept authority or approved flags from model/client JSON. Existing strict expert
result parser is unchanged: this is a prepared sidecar contract, not a registered runtime
feature. No primary model invocation, Resume, client automatic send or new paid service.
Employee viewers must load lessons/assets through membership-checked same-tenant endpoints,
rechecking artifact ownership; no such production endpoint is added in this branch.

## Verification

48 Python tests: typed/bounded content, all eight original capture hashes, areas/order,
read-only reuse SQL against isolated PostgreSQL/WASM, tenant/agent isolation, inactive/
pending sources, archived company, Human/Pause/stale revision and internal-only payload.
Node/JSDOM fake-clock test: all eight slides, autoplay/end, native-pixel ceiling, conditional
mobile focus, no unnecessary desktop zoom, return to overview, pause/resume, manual next/back,
wheel pause, image failures, forbidden origin and cleanup. Provider/browser are not mocked
as successful ERP operations; no ERP operation takes place.
Browser QA uses local preview + standalone embedded-image bundle, desktop and mobile width,
checking controls, screenshot load, scroll bounds and automatic focus. PDF export page 5 was
rendered and inspected after wording update; document remains eight pages.

Integration steps remaining: actual employee review/publish endpoint extension and private
artifact storage, exact known UI build, sidecar wiring into existing call_expert, and secured
/app viewer routing. Do not register browser access or customer sending while doing that.
