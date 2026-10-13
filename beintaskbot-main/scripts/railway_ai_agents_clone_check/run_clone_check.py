#!/usr/bin/env python3
"""Dump production read-only, restore to a private clone, and test only the clone."""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

DATA = Path('/data')
BACKUP_DIR = DATA / 'backup'
PGDATA = DATA / 'postgres' / 'data'
PG_PORT = 55432
SUPPORTED_PG_MAJORS = {14, 15, 16, 17, 18}
MAX_SOURCE_BYTES = 180 * 1024 * 1024
RESERVE_BYTES = 160 * 1024 * 1024
MAX_VOLUME_ESTIMATE = 900 * 1024 * 1024
MAX_VOLUME_BYTES = 5 * 1024 * 1024 * 1024
ALLOWED_TEST_LINES = {
    'PASS: initial schema and migration replay',
    'PASS: tenant-scoped uniqueness and duplicate run rejection',
    'PASS: tenant-filtered job isolation',
    'PASS: Test Mode emergency stop blocks queued work',
    'PASS: concurrent SKIP LOCKED worker claim',
}


class SafeStop(Exception):
    pass


def status(message: str) -> None:
    print(message, flush=True)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafeStop(message)


def validate_volume_capacity(total_bytes: int, mounted: bool = True) -> None:
    require(mounted, 'FAIL: /data is not a mounted Railway volume; no source connection was made')
    require(0 < total_bytes <= MAX_VOLUME_BYTES,
            'FAIL: mounted /data volume exceeds the approved 5 GiB limit; no source connection was made')


def validate_backup_role_policy(role_attrs: tuple[bool, ...] | None,
                                memberships: list[str],
                                can_create_database: bool,
                                can_create_temp: bool,
                                temp_granted_to_public: bool,
                                temp_granted_directly: bool,
                                default_readonly: bool,
    forbidden_object_counts: tuple[int, ...]) -> None:
    require(role_attrs is not None and len(role_attrs) == 7
            and not any(role_attrs[:5]) and role_attrs[5] and role_attrs[6],
            'FAIL: source login has elevated role attributes or cannot log in')
    require(not memberships,
            'FAIL: source login has role memberships; only direct object grants are allowed')
    require(not can_create_database and not temp_granted_directly
            and can_create_temp == temp_granted_to_public and default_readonly,
            'FAIL: source login has a direct TEMP grant, other database creation rights, or lacks the read-only default')
    require(not any(forbidden_object_counts),
            'FAIL: source login privileges do not meet the narrow backup policy')


def clean_postgres_environment() -> dict[str, str]:
    forbidden = {
        'DATABASE_URL', 'PGPASSWORD', 'POSTGRES_PASSWORD', 'POSTGRES_USER',
        'POSTGRES_DB', 'PGHOST', 'PGPORT', 'PGUSER', 'PGDATABASE', 'PGSERVICE',
        'PGSERVICEFILE', 'PGPASSFILE', 'AI_AGENTS_TEST_DATABASE_URL',
        'TENANT_ENCRYPTION_KEY',
    }
    present = sorted(key for key in os.environ if os.environ.get(key) and (
        key in forbidden or key.upper().startswith(('OPENAI_', 'KOMMO_', 'TELEGRAM_', 'AI_AGENTS_'))))
    require(not present, 'FAIL: unexpected database variables are present; no connection was made')
    return {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8'}


def parse_source_url(value: str) -> tuple[str, int, str, str, str, str]:
    parsed = urlsplit(value)
    require(parsed.scheme in {'postgres', 'postgresql'},
            'FAIL: source must be a PostgreSQL connection string')
    host = (parsed.hostname or '').lower().rstrip('.')
    require(host.endswith('.railway.internal'),
            'FAIL: source must use Railway private networking')
    user = unquote(parsed.username or '')
    password = unquote(parsed.password or '')
    database = unquote(parsed.path.lstrip('/'))
    port = parsed.port or 5432
    sslmode = (parse_qs(parsed.query).get('sslmode') or ['prefer'])[0].lower()
    require(sslmode in {'disable', 'allow', 'prefer', 'require'},
            'FAIL: source TLS mode is unsupported; refusing to weaken its settings')
    require(user and password and database, 'FAIL: source connection details are incomplete')
    require(user.lower() not in {'postgres', 'root', 'admin', 'administrator'},
            'FAIL: source login name is administrative; no connection was made')
    return host, port, database, user, password, sslmode


def pgpass_escape(value: str) -> str:
    return value.replace('\\', '\\\\').replace(':', '\\:')


def source_readonly_audit(source_url: str) -> tuple[int, int, str]:
    try:
        import psycopg
    except Exception:
        raise SafeStop('FAIL: PostgreSQL Python driver is unavailable') from None

    host, port, database, user, password, sslmode = parse_source_url(source_url)
    try:
        with psycopg.connect(source_url, connect_timeout=10,
                             options='-c default_transaction_read_only=on') as conn:
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION READ ONLY')
                cur.execute('''SELECT current_user, current_database(),
                    current_setting('server_version_num')::integer,
                    pg_database_size(current_database())''')
                current_user, current_db, version_num, db_bytes = cur.fetchone()
                require(current_user == user and current_db == database,
                        'FAIL: source identity does not match the supplied read-only login')
                cur.execute('''SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication,
                    rolbypassrls, rolinherit, rolcanlogin
                    FROM pg_roles WHERE rolname=current_user''')
                attrs = cur.fetchone()
                cur.execute('''SELECT coalesce(array_agg(parent.rolname ORDER BY parent.rolname), ARRAY[]::name[])
                    FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid
                    WHERE m.member=current_user::regrole::oid''')
                memberships = list(cur.fetchone()[0])
                cur.execute('''SELECT has_database_privilege(current_user,current_database(),'CREATE'),
                    has_database_privilege(current_user,current_database(),'TEMP'),
                    EXISTS (SELECT 1 FROM pg_db_role_setting s
                      WHERE s.setrole=current_user::regrole::oid AND s.setdatabase=0
                      AND 'default_transaction_read_only=on'=ANY(s.setconfig)),
                    EXISTS (SELECT 1 FROM pg_database d CROSS JOIN LATERAL aclexplode(
                      coalesce(d.datacl,acldefault('d',d.datdba))) a
                      WHERE d.datname=current_database() AND a.grantee=0
                        AND a.privilege_type='TEMPORARY'),
                    EXISTS (SELECT 1 FROM pg_database d CROSS JOIN LATERAL aclexplode(
                      coalesce(d.datacl,acldefault('d',d.datdba))) a
                      WHERE d.datname=current_database() AND a.grantee=current_user::regrole::oid
                        AND a.privilege_type='TEMPORARY')''')
                (can_create_db, can_create_temp, default_readonly,
                 temp_granted_to_public, temp_granted_directly) = cur.fetchone()
                cur.execute('''SELECT
                    (SELECT count(*) FROM pg_namespace n
                     WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                       AND (has_schema_privilege(current_user,n.oid,'CREATE') OR
                            NOT has_schema_privilege(current_user,n.oid,'USAGE'))),
                    (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                     WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                       AND c.relkind IN ('r','p','m')
                       AND NOT has_table_privilege(current_user,c.oid,'SELECT')),
                    (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                     WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                       AND c.relkind='S' AND NOT has_sequence_privilege(current_user,c.oid,'SELECT')),
                    (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                     WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                       AND c.relkind IN ('r','p','v','m','f') AND (
                         has_table_privilege(current_user,c.oid,'INSERT') OR
                         has_table_privilege(current_user,c.oid,'UPDATE') OR
                         has_table_privilege(current_user,c.oid,'DELETE') OR
                         has_table_privilege(current_user,c.oid,'TRUNCATE') OR
                         has_table_privilege(current_user,c.oid,'REFERENCES') OR
                         has_table_privilege(current_user,c.oid,'TRIGGER') OR
                         has_any_column_privilege(current_user,c.oid,'INSERT') OR
                         has_any_column_privilege(current_user,c.oid,'UPDATE') OR
                         has_any_column_privilege(current_user,c.oid,'REFERENCES'))),
                    (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                     WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                       AND c.relkind='S' AND (
                         has_sequence_privilege(current_user,c.oid,'USAGE') OR
                         has_sequence_privilege(current_user,c.oid,'UPDATE'))),
                    (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                     WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                       AND c.relowner=current_user::regrole::oid),
                    (SELECT count(*) FROM pg_namespace n
                     WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                       AND n.nspowner=current_user::regrole::oid),
                    (SELECT count(*) FROM pg_largeobject_metadata lo
                     WHERE EXISTS (SELECT 1 FROM aclexplode(
                         coalesce(lo.lomacl, acldefault('L',lo.lomowner))) a
                       WHERE a.grantee IN (0,current_user::regrole::oid)
                         AND a.privilege_type='UPDATE')),
                    (SELECT count(*) FROM pg_largeobject_metadata lo
                     WHERE NOT EXISTS (SELECT 1 FROM aclexplode(
                         coalesce(lo.lomacl, acldefault('L',lo.lomowner))) a
                       WHERE a.grantee IN (0,current_user::regrole::oid)
                         AND a.privilege_type='SELECT')),
                    (SELECT count(*) FROM pg_largeobject_metadata lo
                     WHERE lo.lomowner=current_user::regrole::oid),
                    (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                     WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                       AND c.relkind IN ('r','p','m') AND c.relrowsecurity),
                    (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                     WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                       AND c.relkind='f'),
                    (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                     WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                       AND p.prosecdef AND has_function_privilege(current_user,p.oid,'EXECUTE'))''')
                write_counts = cur.fetchone()
                validate_backup_role_policy(attrs, memberships, can_create_db,
                                            can_create_temp, temp_granted_to_public,
                                            temp_granted_directly, default_readonly,
                                            (write_counts[0], write_counts[3], write_counts[4],
                                             write_counts[5], write_counts[6], write_counts[7],
                                             write_counts[9]))
                require(not any((write_counts[1], write_counts[2], write_counts[8])),
                        'FAIL: source login cannot read every table, sequence, or large object in the backup')
                require(not any(write_counts[10:13]),
                        'FAIL: row-level security, foreign tables, or executable security-definer functions prevent a complete safe dump')
                cur.execute('''SELECT datname, has_database_privilege(current_user,oid,'CONNECT')
                    FROM pg_database WHERE datallowconn AND NOT datistemplate
                      AND datname <> current_database()''')
                other_databases = [(name, bool(can_connect)) for name, can_connect in cur.fetchall()]
                for other_database, can_connect in other_databases:
                    if not can_connect:
                        continue
                    try:
                        with psycopg.connect(host=host, port=port, dbname=other_database,
                                             user=user, password=password, sslmode=sslmode,
                                             connect_timeout=8,
                                             options='-c default_transaction_read_only=on') as other_conn:
                            with other_conn.cursor() as other_cur:
                                other_cur.execute('SET TRANSACTION READ ONLY')
                                other_cur.execute('''SELECT
                                    (SELECT count(*) FROM pg_class c
                                     JOIN pg_namespace n ON n.oid=c.relnamespace
                                     WHERE n.nspname NOT LIKE 'pg_%'
                                       AND n.nspname <> 'information_schema'
                                       AND c.relkind IN ('r','p','v','m','f')
                                       AND has_schema_privilege(current_user,n.oid,'USAGE')
                                       AND (has_table_privilege(current_user,c.oid,'SELECT') OR
                                            has_any_column_privilege(current_user,c.oid,'SELECT'))),
                                    (SELECT count(*) FROM pg_class c
                                     JOIN pg_namespace n ON n.oid=c.relnamespace
                                     WHERE n.nspname NOT LIKE 'pg_%'
                                       AND n.nspname <> 'information_schema'
                                       AND c.relkind='S'
                                       AND has_sequence_privilege(current_user,c.oid,'SELECT'))''')
                                require(not any(other_cur.fetchone()),
                                        'FAIL: source login can read user objects in another database')
                                other_conn.commit()
                    except SafeStop:
                        raise
                    except Exception:
                        raise SafeStop('FAIL: cross-database read access could not be verified') from None
                conn.commit()
    except SafeStop:
        raise
    except Exception:
        raise SafeStop('FAIL: source read-only audit failed; database diagnostics suppressed') from None

    source_major = version_num // 10000
    require(source_major in SUPPORTED_PG_MAJORS,
            'FAIL: source PostgreSQL major version is unsupported')
    require(0 < db_bytes <= MAX_SOURCE_BYTES,
            'FAIL: source database exceeds the 180 MiB safety limit')
    return source_major, db_bytes, host


def run_quiet(argv: list[str], *, env: dict[str, str] | None = None,
              timeout: int = 120) -> None:
    if env is None:
        env = clean_postgres_environment()
    try:
        result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=timeout, check=False)
    except Exception:
        raise SafeStop('FAIL: a database utility could not run; diagnostics suppressed') from None
    require(result.returncode == 0,
            'FAIL: a database utility returned an error; diagnostics suppressed')


def source_pgpass(host: str, port: int, database: str, user: str,
                  password: str) -> Path:
    path = Path('/tmp') / ('pgpass-' + secrets.token_hex(8))
    value = ':'.join(map(pgpass_escape, [host, str(port), database, user, password])) + '\n'
    path.write_text(value, encoding='utf-8')
    path.chmod(0o600)
    return path


def test_only_environment(test_url: str) -> dict[str, str]:
    # The harness receives no source URL, PostgreSQL password, inherited DATABASE_URL,
    # Railway variable references, or production integration credentials.
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
           'AI_AGENTS_TEST_DATABASE_URL': test_url,
           'TENANT_ENCRYPTION_KEY': secrets.token_urlsafe(48)}
    return env


def validate_test_database_url(value: str) -> None:
    parsed = urlsplit(value)
    name = unquote(parsed.path.lstrip('/'))
    require(parsed.scheme in {'postgres', 'postgresql'}
            and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
            and name.startswith('ai_agents_test_'),
            'FAIL: migration target must be an isolated loopback ai_agents_test_* database')


def run_harness(test_url: str) -> None:
    validate_test_database_url(test_url)
    app_root = Path('/opt/app')
    command = [sys.executable, '-u', 'tests/postgres/verify_ai_agents.py']
    try:
        result = subprocess.run(command, cwd=app_root, env=test_only_environment(test_url),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=180, check=False)
    except Exception:
        raise SafeStop('FAIL: PostgreSQL migration/test harness did not complete; diagnostics suppressed') from None
    for line in result.stdout.splitlines():
        if line in ALLOWED_TEST_LINES:
            status(line)
    require(result.returncode == 0,
            'FAIL: PostgreSQL migration/test harness failed; diagnostics suppressed')
    reported = {line for line in result.stdout.splitlines() if line in ALLOWED_TEST_LINES}
    require(reported == ALLOWED_TEST_LINES,
            'FAIL: PostgreSQL harness did not report every required check')


def run_check() -> None:
    clean_postgres_environment()
    volume = shutil.disk_usage(DATA)
    validate_volume_capacity(volume.total, os.path.ismount(DATA))
    if (DATA / 'check.completed').exists() or any(BACKUP_DIR.glob('production-logical-backup-*.dump')):
        raise SafeStop('FAIL: a prior backup or completed check exists; refusing to overwrite or repeat it')
    source_url = os.environ.get('SOURCE_READONLY_DATABASE_URL', '').strip()
    require(bool(source_url),
            'READY: waiting for the dedicated read-only SOURCE_READONLY_DATABASE_URL')
    source_host, source_port, source_db, source_user, source_password, source_sslmode = parse_source_url(source_url)
    source_major, source_size, _ = source_readonly_audit(source_url)
    version_bin = Path(f'/usr/lib/postgresql/{source_major}/bin')
    require(version_bin.is_dir(), 'FAIL: matching PostgreSQL clone tools are unavailable')
    pg_dump = str(version_bin / 'pg_dump')
    pg_restore = str(version_bin / 'pg_restore')
    status(f'PASS: source login verified read-only; PostgreSQL {source_major}')

    volume = shutil.disk_usage(DATA)
    validate_volume_capacity(volume.total, os.path.ismount(DATA))
    free_before = volume.free
    require(source_size * 2 + RESERVE_BYTES < free_before,
            'FAIL: source database plus restore reserve does not fit on the mounted volume')

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dump_path = BACKUP_DIR / f'production-logical-backup-{stamp}.dump'
    require(not dump_path.exists(), 'FAIL: backup filename already exists; refusing to overwrite it')
    pgpass_path = source_pgpass(source_host, source_port, source_db, source_user, source_password)
    source_password = ''
    source_url = ''
    command_env = clean_postgres_environment()
    command_env['PGPASSFILE'] = str(pgpass_path)
    command_env['PGOPTIONS'] = '-c default_transaction_read_only=on'
    command_env['PGSSLMODE'] = source_sslmode
    try:
        run_quiet([pg_dump, '--no-password', '--host', source_host, '--port', str(source_port),
                   '--username', source_user, '--dbname', source_db, '--format=custom',
                   '--lock-wait-timeout=5s', '--file', str(dump_path)],
                  env=command_env, timeout=900)
    finally:
        pgpass_path.unlink(missing_ok=True)
        command_env.pop('PGPASSFILE', None)
    require(dump_path.is_file() and dump_path.stat().st_size > 0,
            'FAIL: logical backup was not produced')

    dump_size = dump_path.stat().st_size
    estimate = source_size * 2 + dump_size + RESERVE_BYTES
    require(estimate <= MAX_VOLUME_ESTIMATE,
            'FAIL: dump and restored copy exceed the 900 MiB safety estimate')
    run_quiet([pg_restore, '--list', str(dump_path)], timeout=120)
    digest_state = hashlib.sha256()
    with dump_path.open('rb') as archive:
        for block in iter(lambda: archive.read(1024 * 1024), b''):
            digest_state.update(block)
    digest = digest_state.hexdigest()
    status(f'PASS: archive verified; {dump_size} bytes; SHA-256 {digest}')

    bin_path = str(version_bin)
    initdb = str(Path(bin_path) / 'initdb')
    pg_ctl = str(Path(bin_path) / 'pg_ctl')
    createdb = str(Path(bin_path) / 'createdb')
    if not (PGDATA / 'PG_VERSION').exists():
        PGDATA.mkdir(parents=True, exist_ok=True)
        run_quiet([initdb, '-D', str(PGDATA), '--username=ai_agents_test',
                   '--auth-local=trust', '--auth-host=trust'], timeout=180)
    require((PGDATA / 'PG_VERSION').read_text(encoding='ascii').strip() == str(source_major),
            'FAIL: restored clone data directory does not match the source version')
    pg_log = Path('/tmp/postgres-check.log')
    run_quiet([pg_ctl, '-D', str(PGDATA), '-l', str(pg_log), '-o',
               '-c listen_addresses=127.0.0.1 -c port=55432 -c max_connections=12 '
               '-c synchronous_commit=off -c full_page_writes=off -c wal_level=minimal '
               '-c max_wal_senders=0', '-w', 'start'], timeout=90)

    test_db = 'ai_agents_test_' + secrets.token_hex(8)
    run_quiet([createdb, '--host=127.0.0.1', f'--port={PG_PORT}',
               '--username=ai_agents_test', test_db], timeout=30)
    target_url = f'postgresql://ai_agents_test@127.0.0.1:{PG_PORT}/{test_db}'
    # pg_restore is the only operation that writes the dump, and its endpoint is
    # deliberately hard-coded to the local clone rather than the source URL.
    run_quiet([pg_restore, '--no-password', '--host=127.0.0.1', f'--port={PG_PORT}',
               '--username=ai_agents_test', '--dbname', test_db, '--no-owner', '--no-acl',
               '--exit-on-error', str(dump_path)], timeout=900)
    status('PASS: archive restored into isolated loopback clone')

    # Migration code runs in a fresh process with exactly one DB URL: the local test DB.
    run_harness(target_url)
    report = BACKUP_DIR / f'check-report-{stamp}.txt'
    report.write_text('\n'.join([
        'status=PASS', f'postgres_major={source_major}',
        f'dump_bytes={dump_size}', f'dump_sha256={digest}',
        f'database_size_bytes={source_size}', 'source_login=read_only_verified',
        'migration_target=isolated_loopback_clone',
        'schema_replay=PASS', 'uniqueness=PASS', 'tenant_isolation=PASS',
        'worker_concurrency=PASS', 'emergency_stop=PASS',
    ]) + '\n', encoding='utf-8')
    report.chmod(0o600)
    (DATA / 'check.completed').write_text('PASS\n', encoding='ascii')
    (DATA / 'check.completed').chmod(0o600)
    status('PASS: all migration and PostgreSQL integration checks complete')
    status('IDLE: stop this Railway service to preserve the dump and volume without compute use')
    while True:
        time.sleep(3600)


if __name__ == '__main__':
    try:
        run_check()
    except SafeStop as exc:
        status(str(exc))
        sys.exit(1)
