# Card refinement integration

Base: production 35886f54. Sources: Sol 7ec42cb6; Terra 16269874.
Wired the agreed optional JSON fields and /improve API into Terra's card editor.
Employee selects remote/phone/chat, records actions, case kind and confirming evidence.
Proposal comparison changes only title/symptoms after explicit Apply; PATCH requires
another explicit Save with expected_version. Dirty edits block approval/improvement.
AI never overwrites cause, resolution or employee actions. No automatic publication.
Analysis language follows RU/AZ/EN; default period is seven local calendar days.

Validation: 89 Python tests plus four Node UI scripts; real Chrome local fixtures at
1280px and390px, including proposal preview and disabled unconfirmed approval.
No schema migration: optional JSON defaults on read; legacy cards preserved in tests.
Worker code, Connect, agent configuration and /webapp unchanged.
Paid real-model verification excluded. Existing finite-batch/restart and deterministic
redaction limitations remain. AI improvement deliberately edits title/symptoms only.
