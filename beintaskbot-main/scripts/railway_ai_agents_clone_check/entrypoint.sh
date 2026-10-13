#!/bin/sh
set -eu

mkdir -p /data/backup /data/postgres
chown postgres:postgres /data /data/backup /data/postgres
exec gosu postgres /opt/venv/bin/python /opt/runner/run_clone_check.py
