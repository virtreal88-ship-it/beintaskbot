"""Default-deny database access for isolated regression runners and children.

Put this directory on PYTHONPATH before launching the test process. PostgreSQL
fixtures create disposable databases on an explicitly configured loopback host;
all application database configuration remains unavailable to the tests.
"""
import psycopg
from psycopg.conninfo import conninfo_to_dict

_connect = psycopg.connect


def isolated_connect(conninfo="", **kwargs):
    options = conninfo_to_dict(conninfo)
    for key in ("host", "hostaddr", "service"):
        if key in kwargs:
            options[key] = kwargs[key]
    loopback = {"localhost", "127.0.0.1", "::1"}
    if options.get("service") or options.get("host") not in loopback:
        raise RuntimeError("External psycopg connection denied by isolated test guard")
    if options.get("hostaddr") and options["hostaddr"] not in loopback:
        raise RuntimeError("External psycopg hostaddr denied by isolated test guard")
    return _connect(conninfo, **kwargs)


psycopg.connect = isolated_connect
