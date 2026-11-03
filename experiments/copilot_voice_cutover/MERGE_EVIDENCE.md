# Legacy writer: merge and clear counterexamples

All experiments use disposable local PostgreSQL and synthetic records. They do
not install a production trigger or change a real role, provider, or account.
The legacy code under test is loaded from the existing Git baseline `fc75c289`.
The voice-shaped turn is synthetic; there is no real voice writer in this test.

## What an update merge can preserve

The old `_finish` constructs an array from a previously read snapshot followed
by the new employee question and AI answer. An isolated `BEFORE UPDATE` trigger
was tested that appends the last two entries from that array to the current
thread. With an existing row, one synthetic voice-shaped turn and two old text
finishes, it preserves all five entries and both completed text receipts.

This is a narrow positive case, **not a proposed production trigger**. It
assumes every legacy update has exactly that suffix shape and does not provide
turn identity, replay protection, caller authority, revision fencing, or an
unambiguous chronological ordering for concurrent operations. Other update
paths, rolling versions, clear, retention and abnormal records need independent
proof. The test trigger and function are dropped after each test.

The old `_finish` does not check whether its `status='sending'` receipt update
affected a row before updating the thread. When the same completed request is
fed to that method again in the experiment, this naive suffix trigger appends
the question/answer pair a second time. It cannot identify the request from the
thread entries alone. This is a demonstrated limitation of the candidate, not
a claim that duplicate dispatch is normal application behavior.

## Clear invalidates the simple merge

The same experiment then invokes the real old `clear`, which deletes the row,
and resumes an old `_finish` holding a pre-clear snapshot. Its upsert becomes an
INSERT, bypasses the UPDATE trigger, and restores the earlier voice-shaped turn.
Thus the tested merge cannot preserve both the voice history and clear behavior.
An INSERT trigger which always discards old snapshot content would erase valid
context in the indistinguishable no-clear case; allowing it can resurrect data.

An independent test invokes real old `clear` for a thread with **no row** and
checks the existing thread and receipt fields before and after. They are equal:
the DELETE affected zero rows and left no scoped clear generation. Given only
those persisted fields, a late old finish cannot distinguish “clear happened”
from “no clear happened”. This is an information limit of the current fields,
not a proof that every conceivable redesign or external evidence source is
impossible.

An additional statement-level PostgreSQL trigger test uses `OLD TABLE` for two
different tenant clears with no matching rows. Both transition tables are empty;
the observed query text is the same parameterized statement and does not reveal
either bound tenant. A global event could be recorded, but it would not identify
the cleared tenant/actor/dialog. Invalidating all simultaneous work globally
would affect unrelated users, and was not implemented or treated as acceptable.
Query-text parsing, timestamps or inferred content are not a scoped correctness
proof. An independently designed, fully scoped capture mechanism could change
the information available; none is established in this repository.

## Remaining gate

The gate remains closed. The tested role/DDL fence blocks a late legacy finish
and loses its saved result; the tested UPDATE merge saves both text results but
restores cleared context. No tested alternative simultaneously preserves the
requested text behavior, voice history and scoped clear semantics. A safe live
cutover still requires a proven legacy-write drain and fence, or a separately
reviewed compatibility protocol that captures exact clear scope and request
generation before **every** legacy writer can act. Do not infer those properties
from a zero receipt count, shared heartbeat, failed HTTP response, or timeout.
