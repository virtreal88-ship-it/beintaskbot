# AI Instructor infrastructure

The `/app/ai-instructor` setup page and `/app/instructions` “Create with AI” entry are available behind the existing tenant-authenticated `/app` API.

## Safety state

- `AI_INSTRUCTOR_ENABLED` defaults to off.
- Demo credentials are encrypted with the existing tenant Fernet key; the API never returns credentials.
- Credentials remain disabled until the server authenticates them and compares the ERP's same-origin identity response with `AI_INSTRUCTOR_EXPECTED_COMPANY_ID`. The identity path and response field names are deployment configuration (`AI_INSTRUCTOR_IDENTITY_PATH`, `AI_INSTRUCTOR_IDENTITY_ACCOUNT_FIELD`, `AI_INSTRUCTOR_IDENTITY_COMPANY_FIELD`). The approved login mapping is configured with `AI_INSTRUCTOR_LOGIN_SUBMIT_PATH` and the three `AI_INSTRUCTOR_LOGIN_*_TEST_ID` values. An operator-issued `AI_INSTRUCTOR_ISOLATION_ATTESTATION_REF` is also required. The browser cannot supply account, company, isolation, or restore proof.
- Verified connections enable the `research` permission level: view and navigate, fill only allowlisted unsaved demo fields, and create private screenshots/video. The executor excludes the save-transfer control in this level. A `demonstration_operation` additionally requires a server-held restore receipt, which may authorize only one job; without it, both UI and API reject the job. Verifying credentials never creates or imports a restore receipt. Viewing does not require inventory restoration.
- A queued task rechecks that its tenant Primary and explicitly linked Expert still exist, are enabled, and remain in Manual mode. Enqueueing or assigning these agents never activates client replies.
- The browser allows only the configured HTTPS origin, pins a public DNS answer, blocks cross-origin requests, WebSockets and downloads, uses an ephemeral context, and records only after successful identity revalidation.
- Artifacts require `AI_INSTRUCTOR_ARTIFACT_DIR` pointing to private storage outside the public asset tree. There is no fallback to a shared temporary directory.
- Interrupted jobs become blocked with an unknown outcome and are never automatically replayed. AI token use is reserved before each injected provider call and capped at 12,000 per job.
- MP4 and screenshots are linked to a Draft in the existing Instructions library. The draft is marked unverified and is never automatically approved or published.

## Current integration boundary

The branch includes the tenant-scoped connection/job/event/artifact store, authenticated API/UI, Playwright executor, Primary/Expert orchestration using the tenant's existing AI settings, subtitle generation, FFmpeg muxing and draft persistence. The background worker is opt-in and starts only when both `AI_INSTRUCTOR_ENABLED` and `AI_INSTRUCTOR_WORKER_ENABLED` are set, private artifact storage and required dependencies are available, and a server-verified demo connection is enabled. The API reports `worker=not_configured` when those runtime checks fail. Local Chromium smoke uses only `tests/fixtures/ai_instructor_demo/` and does not represent the Akul ERP pilot.

Voice generation and TTS providers are out of scope for V1 and are not wired into the instructor.

PostgreSQL integration tests require `AI_INSTRUCTOR_TEST_DATABASE_URL` pointing to a disposable local test database. The production database, ERP credentials, WhatsApp and AI provider are not used by the test suite.

The local `python ui/preview.py` server maps `/app/ai-instructor` to the real page and serves synthetic status responses only. It never loads stored credentials or connects to ERP/AI services.
