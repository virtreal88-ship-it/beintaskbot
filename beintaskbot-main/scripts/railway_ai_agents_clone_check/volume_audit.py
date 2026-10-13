#!/usr/bin/env python3
"""Read-only inventory of the runner's persistent volume; never opens a DB connection."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

DATA = Path('/data')
BACKUP_DIR = DATA / 'backup'
SAFE_REPORT_KEYS = {
    'status', 'postgres_major', 'dump_bytes', 'dump_sha256',
    'database_size_bytes', 'migration_target',
    'schema_replay', 'uniqueness', 'tenant_isolation',
    'worker_concurrency', 'emergency_stop',
}
KEY_RE = re.compile(r'^[a-z][a-z0-9_]*$')
VALUE_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,256}$')
NAME_RE = re.compile(r'^(production-logical-backup-[A-Za-z0-9_.-]+\.dump|check-report-[A-Za-z0-9_.-]+\.txt)$')


def report(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    if not os.path.ismount(DATA):
        report('FAIL: /data is not mounted; volume was not inspected')
        return 1

    report('VOLUME_AUDIT: read_only=true database_connection=none file_writes=none')
    marker = DATA / 'check.completed'
    if marker.is_symlink():
        report('ARTIFACT: check.completed present=true value=SYMLINK_SKIPPED')
    elif not marker.exists():
        report('ARTIFACT: check.completed present=false')
    else:
        try:
            value = marker.read_text(encoding='ascii').strip()
        except Exception:
            value = ''
        report(f'ARTIFACT: check.completed present=true bytes={marker.stat().st_size} '
               f'value={"PASS" if value == "PASS" else "INVALID_OR_UNREADABLE"}')

    files = []
    if BACKUP_DIR.exists() and not BACKUP_DIR.is_symlink():
        for path in sorted(BACKUP_DIR.iterdir(), key=lambda item: item.name):
            try:
                if path.is_file() and not path.is_symlink() and NAME_RE.fullmatch(path.name):
                    files.append((path, path.stat().st_size))
            except OSError:
                report(f'ARTIFACT: {path.name} present=true size=unreadable')
    report(f'BACKUP_FILES: count={len(files)}')
    for path, size in files:
        report(f'ARTIFACT: {path.name} bytes={size}')
        if path.name.startswith('check-report-') and path.suffix == '.txt' and size <= 8192:
            try:
                lines = path.read_text(encoding='utf-8').splitlines()
            except Exception:
                report('CHECK_REPORT: readable=false')
                continue
            safe_lines = []
            for line in lines:
                if '=' not in line:
                    continue
                key, value = line.split('=', 1)
                if key in SAFE_REPORT_KEYS and KEY_RE.fullmatch(key) and VALUE_RE.fullmatch(value):
                    safe_lines.append(f'{key}={value}')
            report('CHECK_REPORT: ' + ('; '.join(safe_lines) if safe_lines else 'no_safe_fields'))
    report('VOLUME_AUDIT: complete')
    return 0


if __name__ == '__main__':
    sys.exit(main())
