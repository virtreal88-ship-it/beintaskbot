# Visual warehouse transfer guide - observed UI verification

Date: 2026-10-18, Asia/Baku. Base: 02af430f, codex/beinsystems-instructor.
Environment: dev.akul.az, visible company Bein Demo, supplied demo account admin@testing.

Observed and exercised through the browser UI only:
- Məhsullar / Yerdəyişmələr; + Yerdəyişmə opened an unsaved form.
- System-provided number/date inspected, not changed.
- Anbardan = Test Anbar; Anbara = test üçün, both visibly applied.
- Product search Beta TEST, result Beta TEST Sud; selected only inside unsaved form.
- Quantity changed from 1 to 2; Tab committed the local field and UI total became 2 əd.
- Digər expanded: Status/Cavabdeh/Şöbə and checked Təsdiqlənib visible; untouched.
- Bağla displayed “Dəyişikliklər yadda saxlanılmayacaq”; Ok discarded changes.
- Returned to existing transfer list with highest visible number 10016; unsaved form number
  10017 was not present in the visible list. No Save/Post operation was invoked.

Search result showed 0 əd; availability of stock is not established. The guide explicitly
warns this example is for form preparation only, not an authorized stock transaction.
No claim of a successful warehouse transfer, posting, persistence or quantity movement.

Still unverified / requires separate authorization to save or post:
- Required-field/insufficient-stock server validation on save.
- Meaning and stock effect of checked Təsdiqlənib, document posting and transaction status.
- Actual source decrease / destination increase, persistence, audit entries and rollback.
- Least privilege, cross-company enforcement and fixture restore remain outside this UI review.

Artifacts: eight distinct original JPEG browser captures, eight SVG vector callout overlays,
SHA-256/rectangle manifest, an eight-page AZ PDF and a ZIP of captures/overlays.
No generated/reconstructed UI images. No passwords/session exports/customer contacts included.
SVG files embed the exact original JPEG bytes; annotations are transparent vector rectangles.
PDF uses the same images and vector overlays, embedded Arial fonts for AZ characters.
Blue frames = inspected controls; red frames = do not save / unverified confirmation setting.

Checks: PDF reopened with pypdf, eight pages and AZ text extracted without replacement glyphs;
all eight pages rendered with Poppler and visually inspected for layout/callout positions.
Source-capture hashes are distinct; local screenshots remain available separately.
No runtime code changes, call_expert, AI Agents, Connect, WhatsApp, Railway, merge or deployment.
