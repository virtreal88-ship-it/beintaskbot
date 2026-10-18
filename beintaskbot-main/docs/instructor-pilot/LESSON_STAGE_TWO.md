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


## Timeline player update

The standalone HTML now has a global 0:00–1:48 seek bar. Default speed is 1.25×
(about 86 seconds of playback); supported rates are 0.75×, 1×, 1.25×, 1.5× and 2×.
Each slide's idle tail is two seconds shorter. The original eight images and AZ copy
remain unchanged. PDF stays the supplementary export and is not regenerated.

Focus geometry and slide fade are computed from one deterministic lesson clock;
there are no independently running CSS transitions. Pause freezes both. Seeking
reconstructs focus, including the middle of a transition. Image loading suspends
clock advancement; pausing during loading cancels automatic continuation. Seeking
and manual slide navigation pause playback. Changing speed preserves position.

Keyboard: Space/K toggles playback outside form controls, arrows seek ±5 seconds,
Home/End seek to lesson bounds. Native range/select/button keyboard interaction is
preserved. Reduced-motion uses immediate focus and disables fades. Mobile controls
remain fixed at the bottom. Geometry is capped at one CSS pixel per source pixel.

Targeted verification: fake-clock player tests cover every speed, live speed change,
all eight slides, forward/backward seek, interpolated focus, frozen pause/resume,
loading cancellation, replay and automatic end, mobile/native ceiling, reduced
motion, keyboard, asset failures/origin protection and timer cleanup. The existing
48 Python contract/isolation tests also pass. Browser QA confirmed 0:50 → step 5,
1:48 total, 1.25× default and mobile controls without horizontal overflow.


### Compact control bar

The bottom bar now uses one icon Play/Pause button, seek slider, elapsed/total time
and a small speed selector. Previous/next buttons and visible routine status text
were removed; screen readers still receive status and AZ action labels. Asset errors
remain visible. All controls retain native keyboard support and 44px touch targets.
Browser checks at 390px and 320px widths show a single approximately 57px row with
no horizontal overflow. Timeline/focus/motion behavior and eight-step content are
unchanged. Targeted player tests pass, including single-button structure and dynamic
accessible Play/Pause labels.


### Real-browser playback diagnosis

The localhost bundle advances actual time and image transforms and automatically
changes steps. A real-clock browser regression page is generated by
`python tests/build_instructor_browser_test.py`; serve `output/lessons` and open
`player-browser-test.html`. It exercises the built HTML and decoded JPEGs, not a
fake clock or only a button label: Play, autofocus, next slide, Pause, Resume,
seek to step 5 and completion at 1:48. JavaScript errors fail the test.

The test reproduced a Pause defect during automatic image loading: the button
was disabled and the playback handler rejected input before checking whether it
should pause. Pause now remains available while loading; resuming still waits
for a valid decoded image. This is not evidence that it caused the reported
initial Play failure, which was not reproduced over localhost.

Direct file:// inspection is blocked by the browser tool's protocol policy;
file-mode compatibility remains unverified and no alternative browser access was
used to bypass that restriction. For a predictable launch run
`python output/lessons/launch_lesson.py` (or double-click with Python association).
It serves only the artifact directory on 127.0.0.1 using an automatically allocated
port and opens the lesson in the default browser. No external dependencies or
production service are needed; Ctrl+C stops it. Keep the window/tab visible:
visibility changes and manual wheel interaction intentionally pause playback.


### Final smooth playback (supersedes earlier speed/fade notes)

Default rate is 1.75×; choices are 0.75×, 1×, 1.25×, 1.5×, 1.75× and 2×.
The 108-second lesson takes about 62 seconds at the default rate. Each slide
returns to overview in its final second (650ms smooth focus transition). A real
650ms crossfade on the lesson clock then blends the previous overview behind the
incoming image; the next focus starts at 1400ms. Rates scale all these timings.
There is no fade to an empty background. Both layers and focus are driven by the
same clock, so Pause freezes their opacity and transform. Reduced-motion disables
crossfade and interpolated focus. Seeking discards the outgoing layer immediately.

The next original JPEG is preloaded and decoded; during a late image load the
previous screenshot remains visible and the lesson clock waits. New captions are
committed with the decoded new slide. Source dimensions, eight images and AZ copy
are unchanged. No image scale exceeds 1.0. Preloads are restricted to the same
validated asset resolver/origin used by the player.

Regression coverage includes all six speeds, pause in mid-crossfade, cleanup of
the outgoing layer, native-size ceiling and existing keyboard/reduced-motion
checks. The real-browser test also performs an uninterrupted full 1.75× run,
records all eight steps, and samples image availability and actual DOM transforms.

Browser result: PASS for real mid-crossfade Pause/Resume, live seek and speed
changes, all eight steps in a complete default-rate run, and sampled image/native
scale checks. Mobile 320/390px checks show no horizontal overflow and a 57px bar.
The 48 Python contract tests and targeted Node player suite pass.


### Azerbaijani narration pilot

Eight separate `output/lessons/audio/step-01.mp3` … `step-08.mp3` clips use Microsoft
az-AZ-BanuNeural via the local edge-tts generation tool. Only the short public
instructional scripts are transmitted, never screenshots, credentials, company
records or chat history. No live TTS request happens in the delivered HTML.
`audio/narration.json` contains scripts, duration, SHA-256, word timings and focus
cues. The generated narration is a pilot awaiting native-speaker listening review,
including Məhsullar, Yerdəyişmələr, Anbardan, Anbara and Miqdar; naturalness is not
claimed as independently verified. Voice support reference:
https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support

The offline bundle embeds all JPEGs and MP3s as local Blob resources under CSP.
No CDN, external runtime request or new service is required. The sound button is
accessible and the only added control. Narration defaults to 1×; silent viewing
to 1.75×, with explicit user speed selections retained when toggling sound.
Pitch preservation is enabled. The original eight step texts/images are unchanged.

With narration enabled the decoded audio currentTime is the lesson clock, followed
by a one-second tail for return to overview and transition. Focus cues use TTS word
boundaries where the relevant UI term is present. Pause/Resume and seeking update
both media position and visuals. Audio failures, rejected playback and eight-second
loading/stall timeout pause the lesson with an instruction to use silent viewing.
The next clip is preloaded. Silent lesson timings stay unchanged.

Rebuild: generate_narration.py (edge-tts/mutagen locally; reuses existing files),
then build_lesson_bundle.py. Generation dependencies are excluded from Git and
production requirements. Open with launch_lesson.py or the existing loopback URL.
The full voice browser regression is built by tests/build_instructor_voice_browser_test.py;
open voice-browser-test.html and click Start voice test (real user gesture required
by browser audio policy). It tests the actual MP3 clock, full eight-clip playback,
Pause/Resume, seek and rate changes. Fake-media targeted tests separately cover
media errors and fallback without claiming an external provider was exercised.

Voice browser result: PASS, all eight real MP3 clips advance in a complete 1×
run and finish at 1:39; real Pause/Resume, seek, rate change and silent toggle pass.
The range control rounds to 10ms, so completion assertions also wait for stopped
play state, rather than treating rounded slider maximum as completion. Targeted
media tests cover pitch preservation, hash/word metadata, stall timeout and error
fallback. The 48 existing Python tests pass. No claim of native-speaker audio QA.


### Subtitle-only pilot (current; supersedes narration)

AZ TTS was rejected after listening review. Audio playback, sound controls, the
8 MP3s, narration metadata, generation script and audio-specific test pages have
been removed. The offline HTML contains JPEGs and text only; no audio requests or
TTS dependencies are used. Playback defaults to 1.75×.

`warehouse-transfer.subtitles.json` supplies short AZ cues on the same lesson
clock as autofocus and crossfade. Pause retains the current cue; seek selects it
deterministically. Caption changes wait for the new screenshot to decode. The
translucent caption strip reserves its own space directly below the screenshot,
so it never overlays important ERP controls. Captions fit one or two lines at
320/390px. Full original explanations and cautions remain under Ətraflı izah.
Eight screenshots, step titles and original source lesson data are unchanged.
Reduced-motion, keyboard, native-resolution ceiling and compact controls remain.

Verification: real-clock localhost browser regression PASS for the full 1.75×
lesson, crossfade Pause/Resume, subtitle retention, matching seek cue and all eight
steps. 320/390px checks confirm one/two-line text in the reserved strip (no image
overlap or horizontal overflow). Targeted Node tests and all 48 Python contract
regressions pass. Rebuild needs only Python standard library, JPEGs and cue JSON.


### Compact overlay captions (current)

The full-width strip is replaced with a centered, text-sized translucent rounded
box inside the image. Height is content-driven and capped at two lines. Placement
uses the currently interpolated image transform and all visible highlight bounds,
trying the bottom first, then safe positions above/below fields. If no safe space
exists in the image, it reserves a small space immediately below it. Screenshots,
focus paths, crossfades, cue text/times, lesson steps and controls are unchanged.

Opacity uses the lesson clock (180ms fade in/out), so Pause freezes it along with
the image. Seeking shows the selected cue immediately; reduced-motion skips fades.
No independent CSS transition continues during Pause. Node regression coverage
adds collision/fallback placement, frozen fade and immediate seek visibility.
The browser test samples caption/highlight intersections and text clipping during
the full lesson in addition to existing playback checks.

Verification: targeted Node suite PASS; real localhost full playback PASS, including
caption collision and two-line clipping checks. Mobile 320/390px visually checked.
