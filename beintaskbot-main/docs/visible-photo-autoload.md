# Visible photo acquisition

Only authenticated Connect photos in the open conversation's viewport are
eligible. A RAM-cache miss is followed by one automatic original acquisition for
that source/version during the current dialog lifecycle. Other media stay
explicit. No archive, retention change, history scan, AI/ASR or outbound send is
introduced. A cold browser/server restart may resolve an original upstream again.

The browser has at most 24 queued visible hosts and two scheduling jobs; Web
Locks serialize automatic work across same-origin tabs when supported. Errors
remain visible. 429 pauses queued work for Retry-After across tenant channels;
failed sources are not retried automatically by polling, rerender or scrolling.
An explicit photo retry remains available after an automatic unavailable result.

The existing attachment endpoint retains current authorization, per-employee and
tenant admission and gains an automatic-photo subset limit of eight cold
acquisitions per tenant/minute. Source-version changes fail before acquisition.
All `tenant_connect_media.download_media` callers, including AI and event loops
in other threads, share two process-wide slots, 20 acquisitions per tenant/minute,
upstream Retry-After and in-flight results fenced by source and authority.
Canceled waiters do not cancel a shared flight. No completed binary cache is
added to that transport. Connect's own admission remains the final bound across
worker processes/replicas; these local counters do not claim distributed state.

Synthetic Python and real-HTML browser fixtures cover warm/cold caches, independent
browser sessions, logout/login, stale source responses, shared UI/AI concurrency,
thread event loops, tenant/channel isolation, 404/429 and explicit retry. Provider
retention remains best effort: an unavailable original is never represented as
archived or as successfully analyzed.
