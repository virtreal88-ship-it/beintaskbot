# Hosted WhatsApp calls engine

The vendored `engine/` contains only source code, module pins and license from
the reviewed local WaCalls experiment, commit
`642106d72341e67518e970eeedcc683f2e2d021e` (2026-11-17), based on
https://github.com/JotaDev66/WaCalls. Codec test vectors are upstream fixtures;
no customer or owner call recordings are included.
No linked-device database, token, signing seed, recordings or browser password
was copied. The MIT license is retained in `engine/LICENSE`.

This build adds a private, per-source supervisor and authenticated PCM streaming.
It retains the tested call registry, provider signaling, consent gate, signed
two-channel recordings, immutable command journal and terminal-event fencing.

Deployment requires one instance, a private persistent `/data` volume, a long
random `CALLS_SERVICE_TOKEN`, and the fixed HTTPS `CALLS_WORKER_URL`. Worker uses
the same token and private `BEINTASKBOT_CALLS_SERVICE_URL`. No public engine or
lab UI domain is needed. Do not mount or copy production Evolution sessions.

Each Connect source is paired once as a separate linked device for its exact
confirmed telephone number. A mismatched account cannot start or accept calls.
The feature has no pilot deadline. New managed recordings are retained for 30
days in Worker; the engine's encrypted delivery spool retains up to 24 hours.
Original pilot recordings keep their original 24-hour expiry.

Source enablement does not grant access to a company or dialog. Worker checks
fresh membership, source grants, route and dialog ownership on actions and while
relaying media. Browser audio is not broadcast, forwarded to arbitrary URLs or
logged. Unknown provider/command outcomes remain blocked. Managed restarts use
an exclusive OS process-lifetime lock plus a persisted boot journal; only a
confirmed former process exit retires its calls, and retired boot IDs can never
reclaim control. This does not rely on a timeout or manual lease reset.
Existing two-minute/20-call daily safety bounds remain enforced and are separate
from the permanent feature lifetime and the 30-day recording retention.

This directory is under development and is not evidence of deployment or live
validation. The hosted network path must pass a consented test with both sides
audible and a recording saved in the same source's dialog before cutover.
