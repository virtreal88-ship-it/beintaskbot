# Voice entry points — UX candidate

Owner-confirmed placement for two independent functions in `/app`:

1. **Customer voice message:** microphone beside the existing message input and send button in the customer dialog. Recording has its own stop, cancel, preview and explicit send; it is not sent to AI.
2. **Employee AI conversation:** voice mode inside the existing dialog AI assistant. Its session, microphone controls and conversation are separate from the customer message composer; it must never send audio to a customer.

`preview.html` is a static, self-contained arrangement study. It has no JavaScript, microphone access, API calls or live application integration. Controls are illustrative and disabled; the mockup is not evidence that either function works.

Existing work remains in separate HOLD candidates: the customer voice UI inserts its microphone before `sendChat` in `.operator-composer-actions` (branch `codex/voice-interaction`, snapshot `a66682521f72ce8e116e6ab686307462b30face9`); the AI candidate mounts a hidden, disabled voice section within the existing assistant (snapshot `06b8b442bf923754972ecf5335ed0793267010fc`). Those are references for coordination, not approved dependencies or a release. Their common composer/helper mounts and microphone exclusion must be reconciled before integration. Respect all current authorization, security, delivery and rollout gates.

The mockup is intentionally standalone: it does not cherry-pick, enable, review, or release either candidate.
