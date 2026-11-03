"""Server-only voice source fingerprints. Does not authorize or send data.

Fingerprints detect observed changes; they cannot atomically fence an external
provider send. The production source gate must remain closed.
"""
from dataclasses import dataclass
from datetime import date,datetime
from decimal import Decimal
import hashlib
import hmac
import json
import uuid
from tenant_linear_policy import TenantLinearError

MAX_JSON_BYTES=2_000_000
MAX_HISTORY_IDS=5000


def _normal(value):
    if value is None or type(value) in (bool,int,str):return value
    if type(value) is float:
        if value!=value or value in (float('inf'),float('-inf')):raise ValueError('nonfinite')
        return value
    if isinstance(value,(datetime,date,uuid.UUID,Decimal)):return str(value)
    if isinstance(value,(tuple,list)):return [_normal(v) for v in value]
    if isinstance(value,dict):
        if any(not isinstance(k,str) for k in value):raise ValueError('nonstring_key')
        return {k:_normal(v) for k,v in value.items()}
    raise ValueError('unsupported_snapshot_value')


def _digest(value):
    try:
        raw=json.dumps(_normal(value),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        if len(raw)>MAX_JSON_BYTES:raise ValueError('snapshot_size')
        return hashlib.sha256(raw).hexdigest()
    except (ValueError,TypeError,RecursionError):
        raise TenantLinearError('Невозможно проверить версию контекста.',409) from None


@dataclass(frozen=True,repr=False)
class SealedSnapshot:
    scope_digest: str
    projection_digest: str
    message_ids_digest: str
    selected_messages: tuple
    thread_digest: str
    knowledge_digest: str

    def matches(self,other):
        if not isinstance(other,SealedSnapshot):return False
        # Compare only fixed-length digests; contents remain server-side.
        return hmac.compare_digest(_digest(self.__dict__),_digest(other.__dict__))


def seal(scope,projection,rows,turns,all_message_ids,knowledge_state,message_records):
    if not isinstance(rows,list) or not isinstance(turns,list) or not isinstance(all_message_ids,list) or not isinstance(message_records,list):
        raise TenantLinearError('Невозможно проверить версию контекста.',409)
    if len(all_message_ids)>MAX_HISTORY_IDS:
        raise TenantLinearError('Слишком большая история для проверки голосового режима.',409)
    ids=[str(row.get('external_id') or '') for row in rows]
    if not ids or any(not value for value in ids) or len(ids)!=len(set(ids)):
        raise TenantLinearError('Идентификаторы сообщений неоднозначны.',409)
    if any(not isinstance(value,str) or not value for value in all_message_ids) or len(all_message_ids)!=len(set(all_message_ids)):
        raise TenantLinearError('Идентификаторы сообщений неоднозначны.',409)
    if not set(ids).issubset(all_message_ids):
        raise TenantLinearError('Источники сообщений изменились.',409)
    records={row.get('external_id'):row for row in message_records if isinstance(row,dict)}
    if len(records)!=len(message_records) or set(records)!=set(ids):
        raise TenantLinearError('Источники сообщений изменились.',409)
    selected=[]
    for identifier,row in zip(ids,rows):
        stored=records[identifier]
        try:row_id=str(uuid.UUID(str(stored['row_id'])))
        except (KeyError,ValueError,TypeError,AttributeError):
            raise TenantLinearError('Идентификатор сообщения недоступен.',409) from None
        selected.append((identifier,row_id,_digest({'selected':row,'stored':stored['record']})))
    return SealedSnapshot(_digest(scope),_digest(projection),_digest(sorted(all_message_ids)),tuple(selected),
                          _digest(turns),_digest(knowledge_state))
