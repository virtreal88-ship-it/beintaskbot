import json
from unittest.mock import MagicMock, call, patch

import tenant_connect_transport as transport


TENANT='00000000-0000-0000-0000-000000000101'
CHANNEL='00000000-0000-0000-0000-000000000102'
CALLBACK='https://crm.example/api/platform/connect/webhook'


class Cursor:
    def __init__(self): self.calls=[]
    def __enter__(self): return self
    def __exit__(self,*_): return False
    def execute(self,sql,params=None): self.calls.append((sql,params))
    def fetchone(self): return {'secrets':b'encrypted-old'}


class Connection:
    def __init__(self): self.value=Cursor();self.commits=0
    def __enter__(self): return self
    def __exit__(self,*_): return False
    def cursor(self): return self.value
    def commit(self): self.commits+=1


def test_reconfigure_reuses_existing_webhook_and_secret():
    connections=[Connection(),Connection()]
    cipher=MagicMock()
    cipher.decrypt.return_value=json.dumps({'api_key':'same-api','webhook_secret':'same-secret','webhook_id':'hook-1'}).encode()
    cipher.encrypt.return_value=b'encrypted-new'
    requests=[]
    def request(key,method,path,data=None):
        requests.append((key,method,path,data))
        if method=='GET' and path=='/v1/channels': return {'data':[{'id':CHANNEL,'name':'Support','status':'connected'}]}
        if method=='GET' and path=='/v1/webhooks': return {'data':[{'id':'hook-1','url':CALLBACK,'active':True}]}
        return {'id':'hook-1'}

    profile={'tenant_id':TENANT,'role':'owner'}
    with patch.object(transport,'_owner'), patch.object(transport,'_connect',side_effect=connections), \
         patch.object(transport,'_schema'), patch.object(transport,'_fernet',return_value=cipher), \
         patch.object(transport,'_json',side_effect=lambda value:json.dumps(value,sort_keys=True)), \
         patch.object(transport,'_request',side_effect=request), patch.object(transport,'status',return_value={'connected':True}):
        result=transport.configure(profile,{'api_key':'same-api','channel_ids':[CHANNEL]},CALLBACK)

    assert result=={'connected':True}
    assert ('same-api','PATCH','/v1/webhooks/hook-1',{'active':True,'events':list(transport.CONNECT_WEBHOOK_EVENTS)}) in requests
    assert not any(method=='POST' and path=='/v1/webhooks' for _,method,path,_ in requests)
    assert not any(path=='/v1/webhooks/hook-1' and data=={'active':False} for _,_,path,data in requests)
    upsert=next(params for conn in connections for sql,params in conn.value.calls if 'INSERT INTO saas_tenant_integrations' in sql)
    saved=json.loads(cipher.encrypt.call_args.args[0])
    assert saved['webhook_secret']=='same-secret' and saved['webhook_id']=='hook-1'
    assert upsert[1]==transport.CONNECT_URL
    assert connections[1].commits==1
