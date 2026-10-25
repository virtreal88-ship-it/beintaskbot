# Scoped media-evidence receipts

`saas_media_evidence_receipts` is an additive receipt cache for the existing
knowledge-extraction pipeline.  A receipt is keyed by tenant, Connect channel,
external message ID and the selected message version hash.  It never stores a
media URL or accepts a caller-supplied URL.

Only terminal evidence is reusable.  `partial` remains partial; in particular,
sampled video frames are not complete video analysis and cannot establish a
solution.  Failed, unavailable and unsupported results are retained to avoid a
blind automatic retry.

## Consumer matrix

| Consumer | Receipt use in this change | New analysis authority |
| --- | --- | --- |
| Knowledge extraction / live queue / campaign | Reads and persists exact scoped receipts | Existing explicit media consent only |
| Primary direct and merged-group Connect history | Read-only labelled context; experts inherit bounded primary lines | None |
| Operator Copilot and support summary | Read-only labelled context after their normal route checks | None |
| Simulation, replay and Beinbot legacy context | Read-only status/context; replay still rejects non-text sources | None |
| Legacy Chat AI | Read-only lookup only for rows that carry exact Connect channel identity | None |
| Instructor | Not a WhatsApp-history consumer; no receipt path | None |

No listed consumer may use a receipt to start a download, ASR, vision call, or
spend.  Existing unavailable/partial markers remain when no exact receipt
exists.  This prevents a cache from becoming an authority bypass.

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
