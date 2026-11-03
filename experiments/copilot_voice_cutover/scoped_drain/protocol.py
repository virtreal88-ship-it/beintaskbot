"""Isolated transaction protocol experiment. Never use as application admission.

All writers must participate; existing workers categorically do not. The caller
must retain a v1 lease until its last possible mutation has completed. No TTL,
process death, or provider timeout may be interpreted as safe completion.
"""


def admit(cur, scope, lease_id):
    """Atomically choose a version and retain a v1 lease before legacy work."""
    cur.execute('''INSERT INTO experiment_scopes (tenant,actor,channel,conversation)
        VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING''', scope)
    cur.execute('''SELECT version FROM experiment_scopes WHERE
        (tenant,actor,channel,conversation)=(%s,%s,%s,%s) FOR UPDATE''', scope)
    version = cur.fetchone()['version']
    if version == 1:
        cur.execute('''INSERT INTO experiment_leases (tenant,actor,channel,conversation,lease_id)
          VALUES (%s,%s,%s,%s,%s)''', (*scope, lease_id))
    return version


def resolve_v1(cur, scope, lease_id):
    """Call only after final legacy mutation has durably completed."""
    cur.execute('''SELECT version FROM experiment_scopes WHERE
        (tenant,actor,channel,conversation)=(%s,%s,%s,%s) FOR UPDATE''', scope)
    row = cur.fetchone()
    if not row or row['version'] != 1:
        raise RuntimeError('invalid legacy resolution')
    cur.execute('''DELETE FROM experiment_leases WHERE
        (tenant,actor,channel,conversation,lease_id)=(%s,%s,%s,%s,%s)''', (*scope,lease_id))
    if cur.rowcount != 1:
        raise RuntimeError('lease not found')


def promote(cur, scope):
    """Promote an existing empty v1 scope; NEVER validates global fleet proof."""
    cur.execute('''SELECT version FROM experiment_scopes WHERE
        (tenant,actor,channel,conversation)=(%s,%s,%s,%s) FOR UPDATE''', scope)
    row = cur.fetchone()
    if not row:
        return False
    if row['version'] == 2:
        return True
    cur.execute('''SELECT EXISTS (SELECT 1 FROM experiment_leases WHERE
        (tenant,actor,channel,conversation)=(%s,%s,%s,%s)) AS pending''', scope)
    if cur.fetchone()['pending']:
        return False
    cur.execute('''UPDATE experiment_scopes SET version=2 WHERE
        (tenant,actor,channel,conversation)=(%s,%s,%s,%s)''', scope)
    return True
