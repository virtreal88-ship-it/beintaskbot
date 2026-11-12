import hashlib
import json
from datetime import datetime, timezone

from tenant_connect_conflict_diagnostics import classify, diagnose

LID='123456789012@lid'; JID='994550000001@s.whatsapp.net'
PAIR={'lid':LID,'jid':JID}

def test_shapes_do_not_grant_identity():
    assert classify({},LID)=='missing_pair'
    assert classify(None,LID)=='unknown_evidence'
    assert classify({'directIdentity':{'lid':LID},'conversationId':LID},LID)=='malformed_or_mismatched_pair'
    raw={'directIdentity':PAIR,'conversationId':LID}
    assert classify(raw,LID)=='paired_shape_compatible'
    assert classify(raw,'994550000099@s.whatsapp.net')=='route_outside_pair'
    assert classify({'directIdentity':PAIR,'originalConversationId':LID,'canonicalConversationId':JID},LID,ledger=True)=='ledger_pair_scope_mismatch'
    assert classify({'directIdentity':PAIR,'originalConversationId':LID,'canonicalConversationId':JID,'pair_matches_conversation':True},LID,ledger=True)=='paired_shape_compatible'

class Cursor:
    def __init__(self): self.sql=[]
    def execute(self,sql,args):
        self.sql.append((sql,args)); self.last=sql; self.read=False
        assert sql.startswith('SELECT ')
    def fetchone(self): return {'present':True,'n':70}
    def fetchmany(self,size):
        assert size == 100
        if self.read: return []
        self.read=True
        return 70 * [{'ref':'00000000-0000-4000-8000-000000000123','stamp':datetime(2026,11,12,tzinfo=timezone.utc),'route':LID,
                 'evidence':{'conversationIdentityConflict':True,'conversationId':LID,'body':'SECRET CUSTOMER TEXT','apikey':'SECRET'}}]

def test_bounded_scoped_redacted_read_only():
    c=Cursor(); result=diagnose(c,'tenant','channel',[LID,JID],lambda *x:hashlib.sha256(repr(x).encode()).hexdigest())
    assert result['classification_is_identity_proof'] is False
    assert len(result['sources'])==3
    assert all(s['total_rows']==70 and s['samples_truncated'] and s['reason_counts']=={'missing_pair':70} for s in result['sources'])
    payload=json.dumps(result)
    for secret in [LID,JID,'SECRET','apikey','body']: assert secret not in payload
    assert result['sources'][0]['samples'][0]['message_ref']=='00000000-0000-4000-8000-000000000123'
    for sql,args in c.sql:
        if 'to_regclass' not in sql:
            assert args[:2]==('tenant','channel')
            assert 'ANY(%s)' in sql
            if ' AS ref' in sql:
                assert 'jsonb_build_object' in sql and 'body' not in sql and 'apikey' not in sql

def test_requires_exact_two_routes():
    c=Cursor(); assert diagnose(c,'t','c',[LID],str)=={'available':False}; assert not c.sql

def test_native_postgres_scope_and_ledger():
    import os
    import uuid
    import pytest
    psycopg=pytest.importorskip('psycopg')
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    dsn=os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN')
    if not dsn: pytest.skip('Disposable localhost PostgreSQL DSN required')
    if conninfo_to_dict(dsn).get('host') not in {'127.0.0.1','localhost','::1'}: raise RuntimeError('localhost only')
    tenant,channel,other=[str(uuid.uuid4()) for _ in range(3)]
    with psycopg.connect(dsn,row_factory=dict_row) as conn:
      with conn.cursor() as cur:
        cur.execute('SET LOCAL search_path TO pg_temp')
        cur.execute('''CREATE TEMP TABLE saas_connect_conversations(tenant_id uuid,connect_channel_id uuid,lead_id bigint,conversation_id text);
          CREATE TEMP TABLE saas_crm_messages(id uuid,tenant_id uuid,kommo_lead_id bigint,channel text,raw jsonb,created_at timestamptz DEFAULT now());
          CREATE TEMP TABLE saas_connect_identity_conflicts(tenant_id uuid,connect_channel_id uuid,conversation_id text,event_id text,evidence jsonb,created_at timestamptz DEFAULT now());
          CREATE TEMP TABLE saas_connect_identity_conflict_events(LIKE saas_connect_identity_conflicts)''')
        for t,c in [(tenant,channel),(other,channel),(tenant,other)]:
          cur.execute('INSERT INTO saas_connect_conversations VALUES(%s,%s,1,%s)',(t,c,LID))
          cur.execute('INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,channel,raw) VALUES(%s,%s,1,%s,%s::jsonb)',(str(uuid.uuid4()),t,'connect:'+c,json.dumps({'conversationIdentityConflict':True,'conversationId':LID})))
          for table in ['saas_connect_identity_conflicts','saas_connect_identity_conflict_events']:
            cur.execute(f'INSERT INTO {table}(tenant_id,connect_channel_id,conversation_id,event_id,evidence) VALUES(%s,%s,%s,%s,%s::jsonb)',(t,c,LID,'provider-event-private',json.dumps({'originalConversationId':LID,'canonicalConversationId':JID,'directIdentity':PAIR,'pair_matches_conversation':True})))
        result=diagnose(cur,tenant,channel,[LID,JID],lambda *x:hashlib.sha256(repr(x).encode()).hexdigest())
        assert [x['total_rows'] for x in result['sources']]==[1,1,1]
        assert result['sources'][0]['reason_counts']=={'missing_pair':1}
        assert result['sources'][1]['reason_counts']=={'paired_shape_compatible':1}
        assert 'provider-event-private' not in json.dumps(result)
        cur.execute('SELECT count(*) AS n FROM saas_crm_messages'); assert cur.fetchone()['n']==3
      conn.rollback()

def test_late_pairless_blocker_counted_after_many_compatible_receipts():
    class Many(Cursor):
        def fetchmany(self,size):
            self.read = int(self.read) + 1
            if self.read > 2: return []
            good={'ref':'private','route':LID,'stamp':None,'evidence':{'conversationId':LID,'originalConversationId':LID,'canonicalConversationId':JID,'directIdentity':PAIR,'pair_matches_conversation':True}}
            bad={**good,'evidence':{}}
            return [good]*100 if self.read == 1 else [good]*80+[bad]
    result=diagnose(Many(),'t','c',[LID,JID],lambda *x:hashlib.sha256(repr(x).encode()).hexdigest())
    for source in result['sources']:
        assert source['reason_counts']=={'paired_shape_compatible':180,'missing_pair':1}
        assert len(source['samples'])==4
        assert source['samples'][-1]['reason']=='missing_pair'


def test_pglite_cursor_fetchmany_consumes_exactly_once():
    from test_tenant_support_inbox import Cursor
    class Connection:
        def execute(self,sql,params): return list(range(5))
    cursor=Cursor(Connection())
    cursor.execute('synthetic')
    assert cursor.fetchmany(2)==[0,1]
    assert cursor.fetchone()==2
    assert cursor.fetchmany(0)==[]
    assert cursor.fetchmany(100)==[3,4]
    assert cursor.fetchmany(100)==[]
    assert cursor.fetchone() is None
    cursor.execute('synthetic')
    assert cursor.fetchmany()==[0]
    assert cursor.fetchall()==[1,2,3,4]
    assert cursor.fetchmany()==[]
