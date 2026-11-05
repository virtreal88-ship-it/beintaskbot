"""Default-deny database access for isolated regression runners and children.

Put this directory on PYTHONPATH before launching the test process. PostgreSQL
fixtures may use loopback or the exact run-scoped socket for the networkless
voice-lease test container; application database configuration remains denied.
"""
import psycopg
from psycopg.conninfo import conninfo_to_dict
import re

_connect = psycopg.connect


def isolated_connect(conninfo="", **kwargs):
    options = conninfo_to_dict(conninfo)
    for key in ("host", "hostaddr", "service"):
        if key in kwargs:
            options[key] = kwargs[key]
    loopback = {"localhost", "127.0.0.1", "::1"}
    host = options.get("host")
    # The voice-lease CI fixture uses a dedicated, networkless Postgres
    # container over a run-scoped Unix socket. Permit only that exact socket
    # and database identity; all other non-loopback connections stay denied.
    isolated_voice_socket = (
        isinstance(host, str)
        and re.fullmatch(r"/tmp/bein-lease-pg\.[A-Za-z0-9]+", host)
        and options.get("dbname") == "voice_lease_test"
        and options.get("user") == "postgres"
        and not options.get("service")
        and not options.get("hostaddr")
    )
    if options.get("service") or (host not in loopback and not isolated_voice_socket):
        raise RuntimeError("External psycopg connection denied by isolated test guard")
    if options.get("hostaddr") and options["hostaddr"] not in loopback:
        raise RuntimeError("External psycopg hostaddr denied by isolated test guard")
    return _connect(conninfo, **kwargs)


psycopg.connect = isolated_connect
