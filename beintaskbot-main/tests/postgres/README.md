# Disposable PostgreSQL check for AI Agents

This uses a fresh, temporary PostgreSQL 16 database and only accepts PostgreSQL URLs on loopback whose database name starts with ai_agents_test_. It never reads credentials from .env and must not be pointed at production.

From the repository root in Linux, WSL, or a machine with Docker Compose, generate a disposable password in the shell environment and run:

    export AI_AGENTS_TEST_PG_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
    export AI_AGENTS_TEST_DATABASE_URL="postgresql://ai_agents_test:$AI_AGENTS_TEST_PG_PASSWORD@127.0.0.1:55432/ai_agents_test_local"
    docker compose -f tests/postgres/compose.yml up -d --wait
    python -m pip install -r requirements.txt
    python tests/postgres/verify_ai_agents.py
    docker compose -f tests/postgres/compose.yml down
    unset AI_AGENTS_TEST_DATABASE_URL AI_AGENTS_TEST_PG_PASSWORD

PowerShell equivalent:

    $env:AI_AGENTS_TEST_PG_PASSWORD = [guid]::NewGuid().ToString('N')
    $env:AI_AGENTS_TEST_DATABASE_URL = "postgresql://ai_agents_test:$($env:AI_AGENTS_TEST_PG_PASSWORD)@127.0.0.1:55432/ai_agents_test_local"
    docker compose -f tests/postgres/compose.yml up -d --wait
    python -m pip install -r requirements.txt
    python tests/postgres/verify_ai_agents.py
    docker compose -f tests/postgres/compose.yml down
    Remove-Item Env:AI_AGENTS_TEST_DATABASE_URL, Env:AI_AGENTS_TEST_PG_PASSWORD

The Compose service binds only to loopback and stores its cluster in a disposable in-memory filesystem. The script runs the real base and AI Agents schema functions twice with migration caches reset, seeds two temporary tenants and exact verified Test Mode bindings, checks tenant-scoped duplicate protection and tenant-filtered reads, verifies an emergency stop blocks an already queued job, then calls the actual claim_next() function from two simultaneous Python workers. Seed rows are deleted at the end. docker compose down removes the disposable service.

Expected output:

    PASS: initial schema and migration replay
    PASS: tenant-scoped uniqueness and duplicate run rejection
    PASS: tenant-filtered job isolation
    PASS: Test Mode emergency stop blocks queued work
    PASS: concurrent SKIP LOCKED worker claim

The script’s database-name guard is deliberate. If it refuses the URL, create a new disposable database whose name starts with ai_agents_test_; do not edit the guard to permit a production name.
