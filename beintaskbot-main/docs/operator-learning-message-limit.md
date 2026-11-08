# Operator learning selection: 30 messages

The dialog’s Send for learning action previews and accepts up to 30 recent messages. It still creates a support case for review, not published knowledge.

Previously the shared snapshot filled its 12,000-character allowance with up to 1,500 characters per message and stopped. Eight long messages could therefore consume the whole selectable window despite the nominal 80-message extraction cap. Multibyte content could also hit the model-context admission check.

The employee capability now uses a 30-message window and divides the existing character allowance among excerpts, reserving attachment placeholder space. Before reservation, training excerpts may shrink further within the existing 16,000-token allowance. Selected IDs and their chronology are never dropped. General extraction still allows 80 messages. Very large agent instructions can still fail closed rather than increasing spend limits.

No media downloads, paid ASR or vision are enabled by this action. Existing tenant/route permissions, assigned-agent revision checks, idempotency, daily/spend reservations and publication review gates are unchanged. No background queue, model tariff or consent setting changes.

Validation uses synthetic SQL fixtures and mocked provider calls: 10/30 long messages, 30 large attachment metadata records, 31 rejection, tenant isolation, repeat-submit idempotency, preserved final provider references, and UI selection/close/reopen checks. Real AI calls and existing submissions are not replayed.
