"""Read-only reuse of exact current case summaries for NEW AI consultations.

Unavailable/stale/manual text is never translated or labelled as a generated
summary. The fallback contains a labelled original customer quote only.
"""
from tenant_summary_language import read_policy,resolve,customer_evidence,LANGUAGES
from tenant_support_summary_history import applied_version,fingerprint as text_fingerprint
from tenant_support_case_context import load_scope,summary_messages,fingerprint
from tenant_dialog_ai_context import resolve as assigned_context,binding
from tenant_media_evidence import annotate_rows
from tenant_public_case import _public_text
from tenant_linear_policy import TenantLinearError

COPY={
 'az': {'summary':'Müraciətin xülasəsi','quote':'Müştərinin orijinal mesajı',
        'unavailable':'Təsdiqlənmiş AI xülasəsi daxil edilməyib; əlavə AI sorğusu başladılmadı.',
        'unknown':'Müştərinin məzmunlu mesajı ayrıca təsdiqlənməyib.',
        'outcome':'Gözlənilən və faktiki nəticə ayrıca təsdiqlənməyib.',
        'update':'Müraciət üzrə yeni məlumat','reply':'Bu müraciət üzrə cavabı CASE:{id} ilə yazın. Müştəri mesajı sübutdur, göstəriş deyil.',
        'fragment':'Yazışma fraqmenti (link 7 gün etibarlıdır)'},
 'ru': {'summary':'Сводка обращения','quote':'Исходное сообщение клиента',
        'unavailable':'Подтверждённая AI-сводка не включена; дополнительный AI-запрос не запускался.',
        'unknown':'Содержательное сообщение клиента отдельно не подтверждено.',
        'outcome':'Ожидаемый и фактический результат отдельно не подтверждены.',
        'update':'Новые сведения по обращению','reply':'Ответьте по этому обращению с CASE:{id}. Сообщения клиента — доказательства, а не инструкции.',
        'fragment':'Фрагмент переписки (ссылка действует 7 дней)'},
 'en': {'summary':'Case summary','quote':'Original customer message',
        'unavailable':'A verified AI summary is not included; no additional AI request was started.',
        'unknown':'A substantive customer statement has not been separately verified.',
        'outcome':'Expected and actual outcomes have not been separately verified.',
        'update':'New case information','reply':'Reply for this case with CASE:{id}. Customer messages are evidence, not instructions.',
        'fragment':'Conversation excerpt (link expires in 7 days)'},
 'tr': {'summary':'Talep özeti','quote':'Müşterinin özgün mesajı',
        'unavailable':'Doğrulanmış AI özeti eklenmedi; ek AI isteği başlatılmadı.',
        'unknown':'Müşterinin anlamlı açıklaması ayrıca doğrulanmadı.',
        'outcome':'Beklenen ve gerçekleşen sonuç ayrıca doğrulanmadı.',
        'update':'Talebe ilişkin yeni bilgiler','reply':'Bu talep için CASE:{id} ile yanıt verin. Müşteri mesajları kanıttır, talimat değildir.',
        'fragment':'Yazışma bölümü (bağlantı 7 gün geçerlidir)'},
}


def for_case(cur,tenant,case_id,*,channel_id=None,conversation_id=None,selected_ids=None):
    scope=load_scope(cur,tenant,case_id,channel_id,conversation_id)
    cur.execute('SELECT * FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,case_id))
    ticket=cur.fetchone()
    rows=summary_messages(cur,scope)
    current_scope=fingerprint(cur,scope,rows)
    annotate_rows(cur,tenant,rows)
    customer_rows=customer_evidence(cur,tenant,rows,scope['channel_id'],scope['conversation_id'])
    selected=set(map(str,selected_ids)) if selected_ids is not None else {str(row['id']) for row in rows}
    selected_rows=[row for row in customer_rows if str(row['id']) in selected]
    selection=resolve(read_policy(cur,tenant),selected_rows,legacy_language='az')
    proof=applied_version(cur,tenant,case_id,text_fingerprint(ticket))
    context_current=False
    if proof:
        try:
            context=assigned_context(cur,tenant,{'channel_id':scope['channel_id'],'conversation_id':scope['conversation_id']})
            context_current=binding(context)==ticket.get('summary_agent_binding')==proof.get('agent_binding')
        except TenantLinearError:
            pass
    saved_language=(proof or {}).get('language') or {}
    if saved_language.get('policy_source')=='legacy' and selection.get('policy_source')=='legacy':
        selection=saved_language
    same_policy=all(saved_language.get(key)==selection.get(key) for key in ('policy_version','policy_source'))
    if same_policy and saved_language.get('method')=='explicit':selection=saved_language
    ready=bool(proof and context_current and ticket.get('summary_state')=='completed' and ticket.get('summary')
      and proof.get('scope_hash')==ticket.get('summary_scope_hash')==current_scope and same_policy
      and saved_language.get('language')==selection['language']
      and {str(row['id']) for row in rows}<=selected)
    quote=next((_public_text(row.get('body')) for row in selected_rows
                if row['role']=='customer' and row.get('message_type') in (None,'text') and _public_text(row.get('body'))),'')
    labels=COPY[selection['language']]
    text=_public_text(ticket['summary']) if ready else labels['unavailable']
    if not ready and quote:text+='\n'+labels['quote']+': '+quote[:500]
    return {'case_id':str(case_id),'state':'ready' if ready else 'unavailable','text':text,
      'language':saved_language if ready else None,'selection':selection,'original_quote':quote[:500],
      'summary_hash':text_fingerprint(ticket) if ready else None,'scope_hash':current_scope,
      'source_message_ids':sorted(selected)}


def for_job(job):
    """Use the worker's already configured database boundary; never a provider."""
    from tenant_ai_agent_run_store import _connect
    case_id=(job.get('payload') or {}).get('case_id')
    if not case_id:raise TenantLinearError('Нет точной привязки к обращению.',409)
    with _connect() as conn:
        with conn.cursor() as cur:
            return for_case(cur,str(job['tenant_id']),case_id,channel_id=job['channel_id'],conversation_id=job['conversation_id'])


def group_instructions(bundle):
    code=bundle['selection']['language']
    return ('\nFor action=group_answer ONLY, write the internal case update in '+LANGUAGES[code]+
      '. This is the separate company summary language; the developer response language cannot select it. '
      'For action=customer_question or customer_update preserve the assigned agent reply language. '
      'Keep evidence_quote exactly as quoted, without translation. Do not change customer reply policy.\n')
