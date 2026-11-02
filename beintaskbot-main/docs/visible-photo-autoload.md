# Visible photo and audio acquisition

Only authenticated Connect photos and voice/audio in the open conversation's viewport are
eligible. A RAM-cache miss is followed by one automatic original acquisition for
that source/version during the current dialog lifecycle. Video/documents stay
explicit. Autoloading adds no archive, history scan, AI/ASR or outbound send.
A cold browser/server restart may resolve an original upstream again.

The attachment server's temporary RAM cache has a 30-minute insertion-based TTL
(1,800 seconds) and the unchanged 32 MiB total/per-entry byte bound per process.
Cache reads do not extend the TTL; LRU pressure can evict a file earlier. Expiry
is lazy: an expired entry is removed on a cache read, not by a timer that promises
physical deletion at exactly 30 minutes. A new worker process starts empty.
Current source/version and access checks still apply to every cache hit.

The browser has at most 24 queued visible hosts and two scheduling jobs; Web
Locks serialize automatic work across same-origin tabs when supported. Without
Web Locks, one scheduling job serializes automatic media in that window, so two
visible files do not compete for the single background transport slot. Other
tabs/foreground work can still cause bounded 429 backpressure. Errors
remain visible. 429 pauses queued work for Retry-After across tenant channels;
failed sources are not retried automatically by polling, rerender or scrolling.
An explicit message-menu retry remains available after an automatic unavailable result.
Voice controls never autoplay. Their redundant lower Load button is hidden;
Load/Retry remains in the message long-press, right-click and keyboard menu.
The former selected-audio browser hint no longer starts its own GET or loads an
offscreen voice. Matching failed/cooldown hints still require a deliberate retry.
Automatic visibility does not create or extend those manual-selection hints.
Three decoded previews remain the limit. Eviction prefers idle/offscreen files,
never a playing audio/video. If all three are playing, queued automatic work
waits for a pause/end; it does not fetch and repeatedly evict visible players.

The existing attachment endpoint retains current authorization, per-employee and
tenant admission and keeps one shared photo/audio subset limit of eight cold
acquisitions per tenant/minute. The legacy internal `automatic_photo` flag and
bucket name cover both kinds; audio does not gain a separate budget or slot.
Source-version changes fail before acquisition. Audio retains its 10 MiB file cap.
All `tenant_connect_media.download_media` callers, including AI and event loops
in other threads, share two process-wide slots, 20 acquisitions per tenant/minute,
upstream Retry-After and in-flight results fenced by source and authority.
Automatic photo/audio owners occupy at most one of those slots; foreground/AI can use
the other. This reserves capacity against background photos, not a guarantee
against contention with other foreground work. First and subsequent canceled
waiters cannot cancel the shared acquisition: a two-thread bounded executor
owns its independent event loop with a 35-second coroutine deadline. Completion,
failure and timeout release the slot even after every caller has gone away.
No completed binary cache is
added to that transport. Connect's own admission remains the final bound across
worker processes/replicas; these local counters do not claim distributed state.

The coroutine deadline is not a hard wall-clock bound for OS DNS. The project
uses `aiohttp>=3.9.0`, does not declare aiodns, and uses aiohttp's DefaultResolver.
The verified Python 3.11 test environment selects ThreadedResolver. A blocking
getaddrinfo/default-executor operation can delay asyncio.run shutdown beyond
35 seconds. Until that operation actually finishes, its physical slot remains
occupied; admission rejects new work rather than submitting an unbounded queue
or pretending the slot was released. This can delay/block media retrieval, but
does not allocate additional media workers. Runtime resolver class and OS DNS
latency were not probed on production. A no-network blocking-resolver regression
checks held slots, no extra submissions and eventual cleanup; changing the DNS
implementation or adding a hard process deadline is outside this change.

Synthetic Python and real-HTML browser fixtures cover warm/cold caches, independent
browser sessions, logout/login, stale source responses, shared UI/AI concurrency,
thread event loops, tenant/channel isolation, 404/429 and explicit retry. Provider
retention remains best effort: an unavailable original is never represented as
archived or as successfully analyzed.
