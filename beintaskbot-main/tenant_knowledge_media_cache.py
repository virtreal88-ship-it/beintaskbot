"""Tenant-scoped ASR receipt cache. Unknown calls are never automatically retried."""
import hashlib
import uuid
from tenant_platform import _connect
from tenant_knowledge_candidates_policy import redact
from tenant_linear_policy import TenantLinearError
import tenant_knowledge_candidates_store as store


def schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_knowledge_media_cache(
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id),fingerprint TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('running','completed','unknown')),
      token UUID NOT NULL,transcript TEXT,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,fingerprint))''')


def transcribe_once(profile,item,model,media,invoke):
    fingerprint=store.digest({'sha256':hashlib.sha256(media[0]).hexdigest(),'model':model,'format':media[2]})
    token=str(uuid.uuid4());tenant=item['tenant']
    with _connect() as conn:
        with conn.cursor() as cur:
            checked,_,_=store.authorize(cur,profile)
            if checked!=tenant:raise TenantLinearError('Нет доступа.',403)
            schema(cur)
            cur.execute('''INSERT INTO saas_knowledge_media_cache(tenant_id,fingerprint,state,token)
              VALUES(%s::uuid,%s,'running',%s::uuid) ON CONFLICT DO NOTHING RETURNING token''',(tenant,fingerprint,token))
            fresh=cur.fetchone()
            if not fresh:
                cur.execute('SELECT state,transcript FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid AND fingerprint=%s',(tenant,fingerprint))
                receipt=cur.fetchone()
                if receipt['state']=='completed':return redact(receipt['transcript'],item.get('redaction_names',[])),{},False
                raise TenantLinearError('Результат предыдущего ASR неизвестен или ещё обрабатывается.',409)
        conn.commit()
    # Mark attempted before crossing the external boundary, even if it throws.
    item['asr_attempted']=True
    try:
        text,usage=invoke(item['key'],model,*media,with_usage=True)
        text=redact(text,item.get('redaction_names',[]))[:4000]
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_knowledge_media_cache SET state='completed',transcript=%s WHERE tenant_id=%s::uuid AND fingerprint=%s AND token=%s::uuid AND state='running'",(text,tenant,fingerprint,token))
            conn.commit()
        return text,usage,True
    except Exception:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_knowledge_media_cache SET state='unknown' WHERE tenant_id=%s::uuid AND fingerprint=%s AND token=%s::uuid",(tenant,fingerprint,token))
            conn.commit()
        raise
