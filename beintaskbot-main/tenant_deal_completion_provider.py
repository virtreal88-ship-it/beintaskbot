"""One completion PATCH: no redirects, OAuth replay or transport retries."""
import asyncio
import json
import requests
from tenant_platform import _connect, _fernet, _ensure_schema
from tenant_chat_send_store import authority
from tenant_linear_policy import TenantLinearError


def credentials(session: dict, lead: int, connection: str) -> dict:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _, identity, integration=authority(cur,session,lead)
            if ':'.join(identity)!=connection:raise TenantLinearError('Kommo hesabı dəyişib.',409)
            tokens=json.loads(_fernet().decrypt(bytes(integration['secrets'])).decode())
            if not tokens.get('access_token'):raise TenantLinearError('Kommo bağlantısını yeniləyin.',409)
            return {'account_domain':integration['account_domain'],'access_token':tokens['access_token']}


def patch(tenant: str, path: str, body: dict, session: dict, connection: str, wait) -> dict:
    if str(session.get('tenant_id'))!=tenant or not path.startswith('leads/') or not path[6:].isdecimal() or body!={'status_id':142}:
        raise ValueError('Invalid completion write')
    active=credentials(session,int(path[6:]),connection)
    wait(active['account_domain'])
    response=requests.patch(f"https://{active['account_domain']}/api/v4/{path}",json=body,
        headers={'Authorization':'Bearer '+active['access_token'],'Accept':'application/json'},timeout=(5,20),allow_redirects=False)
    if response.status_code!=200:raise RuntimeError('Completion result unknown')
    return response.json()


def provider(read, wait):
    async def request(tenant: str, method: str, path: str, *, json_body: dict | None=None, session: dict | None=None, connection: str='') -> dict:
        if method=='GET':return await read(tenant,method,path)
        if method!='PATCH' or session is None:raise ValueError('Invalid completion method')
        return await asyncio.to_thread(patch,tenant,path,json_body,session,connection,wait)
    return request
