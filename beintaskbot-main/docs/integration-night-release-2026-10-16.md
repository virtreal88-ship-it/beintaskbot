# Integrated night operator release — 2026-10-16

Production base: `77d5312a9b89e510a7985c2c2366a3f682e29140`.
Integration branch: `codex/integration-night-operator`, in a separate managed worktree.

## Preserved source history

- Operator workspace: `fd8234a7` (merge `12eb4177`).
- Night agent template: `e2ed1d20` (merge `464d344f`).
- AI reliability: `c3b77bcd` (merge `3b5e130a`).
- Night report: `5ceac038` (merge `41a7ac65`).

## Integration resolutions

Unioned `/app` assets, routes, role permissions and tests. Kept separate operator dialogs and tickets, company/agent/test settings, and one night wizard/AI status entry. Report navigation lives in the Customer service menu. Rebuilt the generated Chakra bundle from merged sources instead of resolving minified output by hand.

Wizard uploads now carry an immutable tenant-validated agent binding. Legacy NULL-bound knowledge stays shared; new private knowledge is retrieved only by its agent, including in the Kommo worker path. No existing company/agent instructions, models or settings are rewritten. A new agent is created disabled in Manual by default; optional explicit activation happens only after handoff and knowledge setup succeeds. The additive nullable column intentionally has no cascading foreign key: deleting an agent never promotes its private sources to shared knowledge or removes source data.

Report reads durable run, event, voice, manual draft and outgoing receipt records, not just the latest coalescing queue job. Unknown sends count as Connect errors. Error coverage is explicitly incomplete for retained historical data; zero retained failures are not displayed as proof of zero actual failures.

Worker recovery keeps accepted sends final, does not replay uncertain model/send outcomes, and retries only before irreversible calls. Existing handoff deduplication and Resume boundaries remain covered by integration tests.

## Validation

- Full Python suite: **1385 passed, 4 skipped, 240 subtests passed**.
- Full JavaScript suite: **53 suites passed**.
- UI production build: passed.
- Added cross-feature tests for disabled Manual creation, Connect scope/schedule, agent knowledge binding, unchanged existing configuration, tenant isolation, durable errors after queue advancement and failed manual draft sends.
- Existing suites cover text/audio/text, AZ/RU audio, decoding, takeover, duplicate webhook/send, worker recovery, support summaries, timezone/DST/night intervals and role enforcement.
- Browser local fixture check at 390px: menu, wrapped actions, night wizard and Connect conversation/Human Mode. Resume additionally covered by automated UI/backend tests; browser confirm automation stalled during the manual fixture check.
- `/webapp` pilot entry files and `bot.py` are unchanged from production base. All new UI assets are mounted only under `/app`.

Four asyncio webhook tests carry an existing Windows loopback skip and still require a Linux runner. Tests use isolated PGlite PostgreSQL and mock AI/Connect transports; no test sends to real contacts. Rare backend error strings retain their original language; navigation, wizard and common diagnostics have RU/AZ/EN translations.

## Deployment

Merge the reviewed integration branch into main, preserving all four source commits. Existing Railway main auto-deployment builds Beintaskbot; startup applies idempotent additive AI schema changes (receipt timestamps/error codes, heartbeat table/indexes and agent knowledge binding). No Connect change or deployment is required for this release; its already deployed voice API remains the dependency. No new Railway services or variables are needed.

After deployment verify the actual commit, worker startup/heartbeat, PostgreSQL queries, `/app` routes/assets, report and pilot `/webapp`. Do not alter Nizam Ai or WhatsApp sessions. Unknown delivery outcomes require human review and are never automatically retried. Historical report errors remain a lower bound.
