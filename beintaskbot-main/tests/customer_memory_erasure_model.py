"""Synthetic two-phase erasure/admission experiment, never production code.

An admission commits before any simulated transport; it is irrevocably in
flight even if bytes have not yet been sent. Pending erasure stops new claims,
not an already admitted request. Logical completion is blocked while requests
remain admitted/unknown; this prototype DOES NOT erase records, validate actual
provider outcomes or fence a paused worker which later resumes. A caller's
outcome label is only an assertion, never evidence. The revision check only *stands in for* an
ACL/identity/source proof; it supplies no real authorization. Direct SQL,
tenant cascade, actual network send/cancellation and production migrations are
outside this model. No text, personal content, credentials or API calls.
"""
import re
from psycopg import sql


class Denied(Exception):
    pass


class ErasureModel:
    def __init__(self, connect, schema):
        if not re.fullmatch(r'synthetic_erasure_[a-f0-9]{32}', schema):
            raise ValueError('synthetic schema required')
        self.connect=connect
        self.schema=sql.Identifier(schema)

    def table(self,name):
        if name not in {'subjects','jobs'}:raise ValueError('invalid synthetic table')
        return sql.SQL('{}.{}').format(self.schema,sql.Identifier(name))

    def install(self,cur):
        cur.execute(sql.SQL('CREATE TABLE {} (tenant UUID NOT NULL, subject UUID NOT NULL, revision BIGINT NOT NULL, state TEXT NOT NULL CHECK(state IN (\'active\',\'pending\',\'completed\')), PRIMARY KEY(tenant,subject))').format(self.table('subjects')))
        cur.execute(sql.SQL('CREATE TABLE {} (tenant UUID NOT NULL, subject UUID NOT NULL, id UUID NOT NULL, kind TEXT NOT NULL CHECK(kind IN (\'model\',\'delivery\')), state TEXT NOT NULL CHECK(state IN (\'queued\',\'admitted\',\'unknown\',\'completed\',\'cancelled\',\'reconciled_completed\',\'reconciled_not_sent\')), PRIMARY KEY(tenant,subject,id), FOREIGN KEY(tenant,subject) REFERENCES {}(tenant,subject))').format(self.table('jobs'),self.table('subjects')))

    def _subject(self,cur,tenant,subject):
        cur.execute(sql.SQL('SELECT revision,state FROM {} WHERE tenant=%s AND subject=%s FOR UPDATE').format(self.table('subjects')),(tenant,subject))
        row=cur.fetchone()
        if not row:raise Denied('subject unavailable')
        return row

    def seed(self,tenant,subject):
        with self.connect() as conn:
            with conn.cursor() as cur:cur.execute(sql.SQL("INSERT INTO {} VALUES(%s,%s,1,'active')").format(self.table('subjects')),(tenant,subject))
            conn.commit()

    def queue(self,tenant,subject,job,kind,expected_revision):
        if kind not in ('model','delivery'):raise Denied('invalid kind')
        with self.connect() as conn:
            with conn.cursor() as cur:
                revision,state=self._subject(cur,tenant,subject)
                if state!='active' or revision!=expected_revision:raise Denied('revoked')
                cur.execute(sql.SQL("INSERT INTO {} VALUES(%s,%s,%s,%s,'queued')").format(self.table('jobs')),(tenant,subject,job,kind))
            conn.commit()

    def admit(self,tenant,subject,job,expected_revision):
        # COMMIT is the logical admission point. It is NOT network delivery.
        with self.connect() as conn:
            with conn.cursor() as cur:
                revision,state=self._subject(cur,tenant,subject)
                if state!='active' or revision!=expected_revision:raise Denied('revoked')
                cur.execute(sql.SQL("UPDATE {} SET state='admitted' WHERE tenant=%s AND subject=%s AND id=%s AND state='queued' RETURNING kind").format(self.table('jobs')),(tenant,subject,job))
                if not cur.fetchone():raise Denied('not queued')
            conn.commit()

    def request_erasure(self,tenant,subject,expected_revision):
        with self.connect() as conn:
            with conn.cursor() as cur:
                revision,state=self._subject(cur,tenant,subject)
                if state!='active' or revision!=expected_revision:raise Denied('changed')
                cur.execute(sql.SQL("UPDATE {} SET state='pending',revision=revision+1 WHERE tenant=%s AND subject=%s").format(self.table('subjects')),(tenant,subject))
                cur.execute(sql.SQL("UPDATE {} SET state='cancelled' WHERE tenant=%s AND subject=%s AND state='queued'").format(self.table('jobs')),(tenant,subject))
            conn.commit()

    def finish_attempt(self,tenant,subject,job,known):
        # A Boolean here is a test assertion, not verified provider evidence.
        if type(known) is not bool:raise Denied('invalid outcome')
        with self.connect() as conn:
            with conn.cursor() as cur:
                self._subject(cur,tenant,subject)
                cur.execute(sql.SQL("UPDATE {} SET state=%s WHERE tenant=%s AND subject=%s AND id=%s AND state='admitted' RETURNING id").format(self.table('jobs')),
                  ('completed' if known else 'unknown',tenant,subject,job))
                if not cur.fetchone():raise Denied('not admitted')
            conn.commit()

    def reconcile(self,tenant,subject,job,evidence):
        # No evidence verification exists in this toy model. An operator label
        # can be wrong, and does not prevent a paused worker from resuming.
        if evidence not in ('operator_asserts_completed','operator_asserts_not_sent'):raise Denied('operator assertion required')
        with self.connect() as conn:
            with conn.cursor() as cur:
                self._subject(cur,tenant,subject)
                outcome=('reconciled_completed' if evidence=='operator_asserts_completed' else 'reconciled_not_sent')
                cur.execute(sql.SQL("UPDATE {} SET state=%s WHERE tenant=%s AND subject=%s AND id=%s AND state='unknown' RETURNING id").format(self.table('jobs')),(outcome,tenant,subject,job))
                if not cur.fetchone():raise Denied('not unknown')
            conn.commit()

    def complete_erasure(self,tenant,subject):
        # Only a logical milestone in synthetic state, not data erasure.
        with self.connect() as conn:
            with conn.cursor() as cur:
                _,state=self._subject(cur,tenant,subject)
                if state!='pending':raise Denied('not pending')
                cur.execute(sql.SQL("SELECT 1 FROM {} WHERE tenant=%s AND subject=%s AND state IN ('admitted','unknown') LIMIT 1").format(self.table('jobs')),(tenant,subject))
                if cur.fetchone():raise Denied('in-flight or unknown')
                cur.execute(sql.SQL("UPDATE {} SET state='completed' WHERE tenant=%s AND subject=%s").format(self.table('subjects')),(tenant,subject))
            conn.commit()

    def state(self,tenant,subject):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL('SELECT revision,state FROM {} WHERE tenant=%s AND subject=%s').format(self.table('subjects')),(tenant,subject))
                subject_row=cur.fetchone()
                cur.execute(sql.SQL('SELECT id,kind,state FROM {} WHERE tenant=%s AND subject=%s ORDER BY id').format(self.table('jobs')),(tenant,subject))
                return subject_row,cur.fetchall()
