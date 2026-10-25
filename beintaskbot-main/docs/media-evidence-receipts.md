# Scoped media-evidence receipts

`saas_media_evidence_receipts` is an additive receipt cache for the existing
knowledge-extraction pipeline.  A receipt is keyed by tenant, Connect channel,
external message ID and the selected message version hash.  It never stores a
media URL or accepts a caller-supplied URL.

Only terminal evidence is reusable.  `partial` remains partial; in particular,
sampled video frames are not complete video analysis and cannot establish a
solution.  Failed, unavailable and unsupported results are retained to avoid a
blind automatic retry.

This first connection deliberately covers manual/live/campaign knowledge
extraction, where external-media consent and route revalidation already exist.
Primary Agent, Copilot, support summary, Simulation, replay, expert and MCP
remain read-only/no-video-analysis consumers: they must continue to present
their existing unavailable state until a separate owner-approved admission
contract is added.  This avoids using a cache to expand their authority.

## Separate limits decision

Preview/download limits are separate from analysis admission.  Knowledge
extraction now has an explicitly approved single-video bound of 100 MiB / 120
seconds; photos remain 20 MiB and audio remains 10 MiB.  The existing four-file,
two-visual and timeout bounds remain, only one video is downloaded in a fragment,
and sampled frames remain partial evidence.  Any further limit change needs a
separate cost, privacy, decoder and owner-consent review.

## One-video procedure after separate approval

Use the existing case-extraction route with its exact tenant/channel/conversation
binding, named source message, `allow_external_media`, and if needed explicit
vision/ASR consent.  Review the terminal receipt and candidate manually.  Do
not use playback, a provider URL, or a historic bulk job as a substitute.
