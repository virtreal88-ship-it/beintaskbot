"""Agent trigger matching and tenant-safe handoff state transitions."""
import re
import uuid
from tenant_ai_dialog_scope import job_scope, lock_dialog
from tenant_ai_agents_schema import ensure_schema
from tenant_platform import _connect, _json

_HANDOFF_TARGET = (
    r"(?:a\s+|the\s+|real\s+)?(?:human|person|someone|somebody|manager|supervisor|"
    r"operator|representative|real agent|support agent|insan\w*|operator\w*|"
    r"menecer\w*|əməkdaş\w*|canlı dəstək\w*|canli destek\w*|"
    r"человек\w*|оператор\w*|менеджер\w*|руководител\w*|специалист\w*)"
)
# These patterns encode a request, rather than a mere mention or reported
# speech (for example, "the operator told me to speak to the sales agent").
_HANDOFF_REQUESTS = tuple(re.compile(pattern, re.I) for pattern in (
    rf"\b(?:can|could) i (?:please )?(?:speak|talk|chat) to {_HANDOFF_TARGET}\b",
    rf"\bi (?:want|need|would like) to (?:speak|talk|chat) to {_HANDOFF_TARGET}\b",
    rf"\b(?:please )?(?:connect|transfer|put|pass|switch) me (?:to|with) {_HANDOFF_TARGET}\b",
    rf"\b(?:i need|i want) {_HANDOFF_TARGET}\b",
    rf"\b{_HANDOFF_TARGET}\s+(?:please|now)\b",
    r"\b(?:insan|operator|menecer|əməkdaş)\w*\s+(?:ilə\s+)?danışmaq\s+istəyir(?:əm|ik)\b",
    r"\b(?:məni\s+)?(?:insan|operator|menecer|əməkdaş)\w*\s+(?:ilə\s+)?(?:danışdırın|qoşun|birləşdirin)\b",
    r"\b(?:хочу|хотим|мне нужно|мне нужен|мне нужна)\s+(?:поговорить\s+с\s+)?(?:человек\w*|оператор\w*|менеджер\w*|специалист\w*)\b",
    r"\b(?:позовите|пригласите)\s*,?\s*(?:пожалуйста\s*,?\s*)?(?:человек\w*|оператор\w*|менеджер\w*|специалист\w*)\b",
    r"\b(?:соедините|переключите|переведите|передайте)\s+(?:меня\s+)?(?:к|на|с)\s+(?:человек\w*|оператор\w*|менеджер\w*|специалист\w*)\b",
))

# Deterministic, high-confidence dissatisfaction signals. These avoid extra
# provider calls and intentionally require an explicit complaint, failed
# resolution, demand, or complaint threat rather than generic negative words.
_DISSATISFACTION = tuple(re.compile(pattern, re.I) for pattern in (
    r"\b(?:i(?:'|’)m|i am)\s+(?:really\s+|very\s+)?(?:dissatisfied|unhappy|disappointed)\b",
    r"\b(?:i(?:'|’)m|i am)\s+(?:really\s+|very\s+)?(?:frustrated|fed up)\s+(?:with|about|by)\s+(?:your\s+|the\s+|this\s+|that\s+)?(?:service|support|response|answer|bot|ai|chatbot|help|handling|problem|issue|you)\b",
    r"\b(?:i am not happy|i am not satisfied|i(?:'|’)m not happy|i(?:'|’)m not satisfied)\s+(?:with|about)\s+(?:your\s+|the\s+|this\s+|that\s+)?(?:service|support|response|answer|bot|ai|chatbot|help|handling|problem|issue)\b",
    r"\b(?:this|that|your (?:service|support|response|answer|bot|ai))\s+(?:is|are|was|were)\s+(?:unacceptable|awful|terrible|useless)\b",
    r"\b(?:your|the)\s+(?:service|support|answers?|bot|ai|chatbot)\s+(?:is|are|keeps?|keep|just)\s+(?:terrible|awful|useless|repeating|giving me the same answer)\b",
    r"\b(?:i have|i've|we have|we've|i|we)\s+(?:already\s+)?(?:told|contacted|written to|called)\s+you\b.{0,100}\b(?:still not fixed|still unresolved|not resolved|not fixed|problem remains)\b",
    r"\b(?:again|yet again|for the \w+ time|several times|multiple times|repeatedly)\b.{0,100}\b(?:still not working|not fixed|not resolved|problem remains|same problem)\b",
    r"\b(?:i|we)\s+(?:demand|insist)\s+(?:that\s+)?(?:you\s+)?(?:fix|resolve|solve|address)\b",
    r"\b(?:i will|i'm going to|we will)\s+(?:file|make|submit|lodge)\s+(?:a\s+)?complaint\b|\b(?:i will|i'm going to)\s+report you\b",
    r"\b(?:xidmətdən|xidmətinizdən|dəstək xidmətindən)\s+(?:çox\s+)?narazı(?:yam|yıq)?\b",
    r"\b(?:bu|belə)\s+(?:xidmət|cavab|yanaşma)\s+(?:qəbuledilməzdir|qəbulolunmazdır)\b",
    r"\b(?:cavablarınız|cavabınız|bot(?:unuz)?|ai)\b.{0,60}\b(?:məni qane etmir|eyni cavabı təkrarlayır|kömək etmir)\b",
    r"\b(?:dəfələrlə|neçə dəfə|yenə|təkrarən)\b.{0,100}\b(?:həll olunmayıb|düzəlməyib|işləmir|problem qalır|cavab almadım)\b",
    r"\b(?:tələb edirəm|xahiş edirəm)\b.{0,80}\b(?:problemi|məsələni)\b.{0,40}\b(?:dərhal|indi)\b.{0,30}\bhəll edin\b",
    r"\bşikayət\s+(?:edəcəyəm|edəcəm|etmək məcburiyyətində qalacağam)\b|\b(?:rəsmi\s+)?şikayət\s+(?:edəcəyəm|edəcəm)\b",
    r"\bя\s+(?:очень\s+)?(?:недоволен|недовольна|разочарован|разочарована|раздражён|раздражена)\s+(?:(?:вашим|вашей|этим|этой|работой)\s+)?(?:обслуживанием|сервисом|поддержкой|ответами|ответом|ботом|ии|решением|работой|помощью)\b",
    r"\b(?:это|такое|ваш сервис|ваша поддержка|ваш ответ)\s+(?:совершенно\s+)?(?:неприемлемо|ужасно|отвратительно|бесполезно)\b",
    r"\b(?:ваши ответы|ответы бота|ответы ии|бот|ии)\b.{0,60}\b(?:меня не устраивают|повторяет одно и то же|не помогают)\b",
    r"\b(?:я|мы)\s+(?:уже\s+)?(?:несколько раз|много раз|снова|опять|в который раз)\b.{0,100}\b(?:не решен[аоы]?|не исправлен[аоы]?|не работает|проблема осталась|не помогло)\b",
    r"\b(?:я|мы)\s+(?:требую|настаиваю)\b.{0,80}\b(?:решить|исправить|устранить)\b",
    r"\b(?:я|мы)\s+(?:подам|подаём|подадим|напишу|напишем)\s+(?:официальную\s+)?жалобу\b|\bпожалуюсь\b",
))
_QUOTED_SPAN = re.compile(r'“[^”]*”|«[^»]*»|„[^“]*“|‘[^’]*’|"[^"\n]*"|`[^`]*`|(?<!\w)\x27[^\x27\n]+\x27(?!\w)', re.S)
_REPORTED_COMPLAINT = re.compile(
    r"\b(?:(?:customer|client|user|colleague|coworker|friend|boss|someone|they|he|she)|(?:my|our)\s+(?:customer|client|colleague|coworker|friend|boss))\s+(?:said|wrote|mentioned|reported|complained|asked)\b|"
    r"\b(?:müştəri|musteri|alıcı)\s+(?:dedi|yazdı|bildirdi|xahiş etdi|şikayət etdi)\b|"
    r"\b(?:(?:клиент|покупатель|коллега|друг|подруга|начальник|он|она|они)|(?:мой|моя|наш|наша)\s+(?:клиент|покупатель|коллега|друг|подруга|начальник))\s+(?:сказал\w*|написал\w*|сообщил\w*|попросил\w*|пожаловал\w*)\b",
    re.I,
)


def handoff_reason(text):
    body = str(text or '')[:6000]
    complaint = _QUOTED_SPAN.sub(' ', body).casefold()
    if _REPORTED_COMPLAINT.search(complaint):
        return None
    if any(pattern.search(complaint) for pattern in _HANDOFF_REQUESTS):
        return 'customer_requested_manager'
    if any(pattern.search(complaint) for pattern in _DISSATISFACTION):
        return 'customer_dissatisfied'
    return None


def should_handoff(text):
    return handoff_reason(text) is not None


def enabled_triggers(tenant_id,agent_id):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT id,name,trigger_type,condition,actions FROM saas_ai_agent_triggers
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND enabled=TRUE ORDER BY created_at,id''',(tenant_id,agent_id))
            return [dict(row) for row in cur.fetchall()]


def matching_triggers(triggers,text,*,first_inquiry=False,phone=''):
    body=str(text or '').casefold()
    matches=[]
    for trigger in triggers:
        kind=trigger.get('trigger_type');condition=trigger.get('condition') or {}
        if kind=='new_inquiry' and first_inquiry:matches.append(trigger)
        elif kind=='phone_received' and phone:matches.append(trigger)
        elif kind=='phrase' and any(phrase.strip().casefold() in body for phrase in
            str(condition.get('phrase') or '').split('|') if phrase.strip()):matches.append(trigger)
    return matches


def handoff(job,reason,*,source_message_id='',expected_conversation_revision=None,case_message_ids=None):
    from tenant_support_inbox_store import handoff_ticket, summarize_created, handoff_recorded
    ticket = None
    tenant=str(job['tenant_id']);chat=str(job['chat_id'])
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            lock_dialog(cur, tenant, job["provider"], job.get("channel_id"), chat)
            if source_message_id:
                # A generated handoff is valid only while this exact inbound
                # remains the newest customer event and the claimed worker
                # lease/conversation revision are still current. Ingestion,
                # Pause and takeover serialize on this same dialog lock.
                if job.get('provider')!='connect' or not job.get('lease_token'):
                    return False
                scope=job_scope(job)
                cur.execute('''SELECT j.state,j.lease_token,j.revision,
                    j.lease_until>clock_timestamp() AS lease_live
                  FROM saas_ai_agent_jobs j WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid
                    AND j.provider='connect' AND j.conversation_id=%s AND j.scope_channel_id=%s
                    AND j.event_fingerprint=%s FOR UPDATE''',
                  (tenant,job['agent_id'],chat,scope,job.get('event_fingerprint')))
                claimed=cur.fetchone() or {}
                if (claimed.get('state')!='processing' or claimed.get('lease_token')!=job.get('lease_token')
                        or int(claimed.get('revision') or 0)!=int(job.get('revision') or 0)
                        or claimed.get('lease_live') is not True):
                    return False
                cur.execute('''SELECT state,revision FROM saas_ai_conversation_state
                  WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s
                    AND conversation_id=%s FOR UPDATE''',(tenant,scope,chat))
                state=cur.fetchone() or {}
                if (state.get('state')!='active' or
                        (expected_conversation_revision is not None
                         and int(state.get('revision') or 0)!=expected_conversation_revision)):
                    return False
                try: source_key=str(uuid.UUID(str(source_message_id)))
                except (ValueError,TypeError,AttributeError): return False
                cur.execute('''SELECT external_id FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND direction='incoming'
                  ORDER BY COALESCE(happened_at,created_at) DESC,id DESC LIMIT 1''',
                  (tenant,int(job['lead_id']),'connect:'+scope))
                latest=cur.fetchone() or {}
                if latest.get('external_id')!='connect:'+source_key:
                    return False
            if handoff_recorded(cur, job):
                return False
            if job['provider'] == 'connect':
                from tenant_ai_group_coordination import stop
                stop(cur, tenant, chat, reason=reason)
            cur.execute('''UPDATE saas_ai_conversation_state SET state='human',reason=%s,revision=revision+1,updated_at=now()
              WHERE tenant_id=%s::uuid AND provider=%s AND conversation_id=%s AND channel_id=%s AND state<>'human' RETURNING agent_id''',
              (str(reason)[:240],tenant,job['provider'],chat,job_scope(job)))
            changed=cur.fetchone()
            ticket = handoff_ticket(cur,{**job,'source_message_id':source_message_id,
                'case_message_ids':case_message_ids or []},reason)
            if changed:
                cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,error_code='human_handoff',updated_at=now() WHERE tenant_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s AND state IN ('queued','processing')",(tenant,job['provider'],chat,job_scope(job)))
                cur.execute("UPDATE saas_ai_agent_runs SET state='superseded',updated_at=now() WHERE tenant_id=%s::uuid AND event_fingerprint=%s AND state='generating'",(tenant,job['event_fingerprint']))
                cur.execute("UPDATE saas_tenant_webhook_events SET state='completed',completed_at=now(),error_code='human_handoff' WHERE tenant_id=%s::uuid AND fingerprint=%s",(tenant,job['event_fingerprint']))
                cur.execute("SELECT telegram_id FROM saas_tenant_members WHERE tenant_id=%s::uuid AND active=TRUE AND role IN ('owner','admin')",(tenant,))
                for member in cur.fetchall():
                    cur.execute('''INSERT INTO saas_tenant_notifications(id,tenant_id,telegram_id,event_key,title,body,target_url)
                      VALUES(%s,%s::uuid,%s,%s,'AI: менеджер должен ответить',%s,%s)''',
                      (uuid.uuid4(),tenant,member['telegram_id'],'ai_handoff:'+chat+':'+job['event_fingerprint'],str(reason)[:240],'/app?view=deals&deal_id='+str(job['lead_id'])))
        conn.commit()
    summarize_created(tenant, ticket)
    return bool(changed)
