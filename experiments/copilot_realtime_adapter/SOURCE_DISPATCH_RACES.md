# Synthetic PostgreSQL source-dispatch counterexamples

This test-only experiment is based on commit `8417b28fa408d6c43804a52c5acbe18fe1ae1566`.
It exercises the actual `Session` dispatch and `CopilotContext` against
disposable local PostgreSQL. The provider, ledger and lease are synthetic;
the source gate is bypassed only inside the test process. It performs no
provider request, paid call, production access, or production configuration
change. The immutable production gates must remain closed.

The inherited `RealVoiceAuthority` fixture refuses non-local database hosts,
URL query parameters, unexpected `PG*` variables, and any database name other
than `test_voice_auth_` followed by exactly 32 lowercase hexadecimal digits.
It creates and removes a random schema in that database. Use a fresh,
disposable PostgreSQL server with synthetic credentials and no real data.

Set `VOICE_AUTH_PG_URL` to that server's local URL and run from the repository
root with the project's Python dependencies installed:

```sh
PYTHONPATH=beintaskbot-main:experiments/copilot_realtime_adapter \
  python -m unittest -v \
  test_source_dispatch_races_pg.ResponseRaces.test_change_after_claim_before_final_check \
  test_source_dispatch_races_pg.ResponseRaces.test_change_after_final_check_before_response_create
```

Each test uses asynchronous barriers, commits the change from another
PostgreSQL connection, and then releases the dispatcher. For edit, new message,
and revocation before the final check, dispatch is refused. The same changes
after that check but before synthetic `response.create` do not prevent it.
An edit followed by an exact reversion (ABA) is not detected even before the
final check. The successful assertions demonstrate these limitations; they
are not proof of a safety fence or production readiness. The separate first
provider context transmission and in-flight output require their own fences.
