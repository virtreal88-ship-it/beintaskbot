"""Single-process synthetic cut/start experiment, never an application transport.

Only an explicitly opted-in loopback PostgreSQL database named
customer_memory_test and a hardcoded loopback HTTP endpoint are permitted.
Loopback and the database name do not prove it is disposable: the test
infrastructure must be verified separately. No production integration, real
customer payload or AI client are accepted. An in-process mutex is NOT
distributed fencing and provides no cross-process or crash guarantee.
"""
import http.client
import os
import re
import threading
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

_SCHEMA=re.compile(r'^synthetic_memory_transport_[0-9a-f]{32}$')
_BODY=b'synthetic fixture only'


def _connection(dsn):
    params=conninfo_to_dict(dsn)
    host=params.get('host')
    hostaddr=params.get('hostaddr')
    if (os.environ.get('CUSTOMER_MEMORY_TEST_ISOLATED')!='1'
            or params.get('dbname')!='customer_memory_test' or params.get('service')
            or os.environ.get('PGSERVICE')
            or host not in {'127.0.0.1','::1','localhost'}
            or hostaddr and hostaddr not in {'127.0.0.1','::1'}):
        raise RuntimeError('Only the explicitly isolated loopback database is supported')
    # Explicit hostaddr and connect_timeout override libpq environment defaults.
    actual=hostaddr or ('::1' if host=='::1' else '127.0.0.1')
    return psycopg.connect(dsn,hostaddr=actual,connect_timeout=2)


def _schema(schema):
    if not isinstance(schema,str) or not _SCHEMA.fullmatch(schema):
        raise ValueError('Synthetic schema required')
    return sql.Identifier(schema)


def _id(value):
    return str(uuid.UUID(str(value)))


def create_test_schema(dsn,schema):
    name=_schema(schema)
    with _connection(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL('CREATE SCHEMA {}').format(name))
            cur.execute(sql.SQL('''CREATE TABLE {}.gate (
                tenant UUID NOT NULL, subject UUID NOT NULL,
                pending BOOLEAN NOT NULL DEFAULT FALSE, version BIGINT NOT NULL DEFAULT 1,
                PRIMARY KEY(tenant,subject))''').format(name))
        conn.commit()


def add_test_subject(dsn,schema,tenant,subject):
    name=_schema(schema);tenant=_id(tenant);subject=_id(subject)
    with _connection(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL('INSERT INTO {}.gate(tenant,subject) VALUES(%s,%s)').format(name),(tenant,subject))
        conn.commit()


class LocalCoordinator:
    """Share exactly this instance between *all* local cut/start operations.

    A reservation is an ephemeral observed version, not a durable job receipt.
    `send` returns after an actual localhost write; provider acceptance and
    remote receipt may be unknown, so no automatic retry is performed.
    """
    def __init__(self,dsn,schema,tenant,subject,port):
        self.dsn=dsn;self.schema=_schema(schema);self.tenant=_id(tenant);self.subject=_id(subject)
        if type(port) is not int or not 1<=port<=65535:raise ValueError('Synthetic port required')
        self.port=port
        self._mutex=threading.Lock()

    def reserve(self):
        with self._mutex:
            with _connection(self.dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql.SQL('SELECT pending,version FROM {}.gate WHERE tenant=%s AND subject=%s').format(self.schema),(self.tenant,self.subject))
                    row=cur.fetchone()
                conn.commit()
            return int(row[1]) if row and row[0] is False else None

    def mark_pending(self):
        """Commit durable revocation while holding the same process mutex."""
        with self._mutex:
            with _connection(self.dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql.SQL('''UPDATE {}.gate SET pending=TRUE,
                      version=version+CASE WHEN pending THEN 0 ELSE 1 END
                      WHERE tenant=%s AND subject=%s RETURNING version''').format(self.schema),(self.tenant,self.subject))
                    row=cur.fetchone()
                conn.commit()
            return int(row[0]) if row else None

    def send(self,reserved_version,*,before_write=None,after_write=None):
        """Recheck, close DB transaction, then write only a synthetic local POST.

        The shared mutex remains held from recheck through local socket write;
        it is released before waiting for a response. Test callbacks are only
        scheduling barriers. This cannot cancel bytes already written and it
        cannot fence another process or a second independent coordinator.
        """
        if type(reserved_version) is not int or reserved_version<1:return 'denied'
        client=None
        try:
            with self._mutex:
                with _connection(self.dsn) as conn:
                    with conn.cursor() as cur:
                        cur.execute(sql.SQL('SELECT pending,version FROM {}.gate WHERE tenant=%s AND subject=%s').format(self.schema),(self.tenant,self.subject))
                        row=cur.fetchone()
                    conn.commit()
                if not row or row[0] is not False or int(row[1])!=reserved_version:return 'denied'
                if before_write is not None:before_write()
                # HTTPConnection never consults an HTTP proxy. Both target and
                # body are fixed synthetic values, never supplied by a caller.
                client=http.client.HTTPConnection('127.0.0.1',self.port,timeout=2)
                client.request('POST','/synthetic',body=_BODY,
                             headers={'Content-Type':'application/octet-stream','Content-Length':str(len(_BODY))})
                if after_write is not None:after_write()
            # Waiting for the remote acknowledgement never holds the mutex or
            # a PostgreSQL transaction. Failure is unknown, with no retry.
            response=client.getresponse()
            response.read()
            return 'accepted' if response.status==200 else 'unknown'
        except (OSError,http.client.HTTPException):
            return 'unknown'
        finally:
            if client is not None:client.close()
