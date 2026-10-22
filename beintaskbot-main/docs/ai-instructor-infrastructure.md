# AI Instructor infrastructure

The `/app/ai-instructor` setup page and `/app/instructions` “Create with AI” entry are available behind the existing tenant-authenticated `/app` API.

## Safety state

- `AI_INSTRUCTOR_ENABLED` defaults to off.
- Demo credentials are encrypted with the existing tenant Fernet key; the API never returns credentials.
- A connection remains disabled until server-side demo-company isolation and fixture-restore evidence are verified. Those references cannot be supplied by a browser request.
- A queued task rechecks that its tenant Primary and explicitly linked Expert still exist, are enabled, and remain in Manual mode. Enqueueing or assigning these agents never activates client replies.
- The browser allows only the configured HTTPS origin, pins a public DNS answer, blocks cross-origin requests, WebSockets and downloads, uses an ephemeral context, and records only after authentication.
- Artifacts require `AI_INSTRUCTOR_ARTIFACT_DIR` pointing to private storage outside the public asset tree. There is no fallback to a shared temporary directory.
- Interrupted jobs become blocked with an unknown outcome and are never automatically replayed. AI token use is reserved before each injected provider call and capped at 12,000 per job.
- MP4 and screenshots are linked to a Draft in the existing Instructions library. The draft is marked unverified and is never automatically approved or published.

## Current integration boundary

This branch provides the tenant-scoped connection/job/event/artifact store, authenticated API/UI, Playwright executor, injected Primary/Expert orchestration, subtitle generation, FFmpeg muxing and draft persistence. It deliberately does not start a background worker or bind model providers yet; the API reports `worker=not_configured`, and jobs cannot be run until the verified demo and worker/provider binding exist. The local Chromium smoke uses only `tests/fixtures/ai_instructor_demo/` and does not represent the Akul ERP pilot.

Voice generation and TTS providers are out of scope for V1 and are not wired into the instructor.

PostgreSQL integration tests require `AI_INSTRUCTOR_TEST_DATABASE_URL` pointing to a disposable local test database. The production database, ERP credentials, WhatsApp and AI provider are not used by the test suite.
