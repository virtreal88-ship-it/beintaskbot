# Isolated Copilot Voice UI prototype

This directory contains only the experimental browser audio capture/playback
module, its unmounted controller, and local unit tests. The current `/app`
does not load or import these files. No server route, credential, provider
connection, feature flag, database change, migration, or Copilot history writer
is included.

The controller accepts dependencies by injection; this checkout does not
provide a production connection or mount it in the app. The tests use synthetic
media, lifecycle objects, and callbacks and do not make network or paid API
calls.

This is not a production voice feature. Capture currently requires a browser
AudioContext that reports exactly 24 kHz and uses the deprecated
`ScriptProcessorNode`; mobile/browser compatibility and real Realtime transport
have not been validated. Keep it unmounted until those issues and the separate
history, budget, and session-lifecycle requirements are accepted.

Run the isolated tests from this directory:

```sh
node --test test_audio.cjs test_browser.cjs
```
