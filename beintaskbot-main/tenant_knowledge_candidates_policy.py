"""Bounded extraction, conservative privacy redaction and strict review cards."""
import re
from tenant_linear_policy import TenantLinearError, identifier
MAX_MESSAGES=80
MAX_CONTEXT=12000
MAX_TOKENS=16000
MAX_DAILY_EXTRACTIONS=10
MAX_CARDS=5
BASE_FIELDS={'title','symptoms','cause','resolution_steps','outcome','source_message_ids','product','product_version'}
FIELDS=BASE_FIELDS|{'resolution_method','operator_actions','operator_action_evidence','cause_evidence','case_kind','content_language','clarification_note','work_status','start_message_id','end_message_id','case_relation'}

def language(value=None,profile=None):
    # AI-created case content has a fixed Azerbaijani language contract. Keep
    # accepting legacy RU/EN request values, but never let UI locale change the
    # generated case language. Staff edits remain unrestricted in card policy.
    if value not in (None, '', 'ru', 'az', 'en'): raise TenantLinearError('Язык: ru, az или en.')
    return 'az'


def redact(text,names=()):
    text=str(text or '')[:30000]
    known={str(n).strip() for n in names if n}
    known.update(part for name in list(known) for part in name.split() if len(part)>=3 and part[:1].isupper())
    for name in sorted(known,key=len,reverse=True):
        if len(name)>=2: text=re.sub(r'(?<!\w)'+re.escape(name)+r'(?!\w)','[PERSON]',text,flags=re.I)
    text=re.sub(r'-----BEGIN [\s\S]*?-----END [^-]+-----','[SECRET]',text)
    text=re.sub(r'(?im)(?:api[_ -]?key|authorization|bearer|password|passwd|пароль|парол|ключ|секрет|код сопряжения|şifrə|token|токен|pairing|pin|cookie|secret)\s*(?:[:=]|\bis\b|—|-)?\s*[^\n]+','[SECRET]',text)
    text=re.sub(r'(?i)\b(?:https?|postgres(?:ql)?|redis|mongodb)://[^\s<>]+','[URL]',text)
    text=re.sub(r'(?i)\b(?:sk-[\w-]{8,}|eyJ[\w-]+\.[\w-]+\.[\w-]+)\b','[SECRET]',text)
    text=re.sub(r'(?i)\b[A-F0-9]{24,}\b','[SECRET]',text)
    text=re.sub(r'(?i)\b[A-Z]{2}\d{2}[A-Z0-9]{12,30}\b','[ACCOUNT]',text)
    text=re.sub(r'(?im)(?:меня зовут|my name is|имя|фамилия|surname|adım)\s*[:=]?\s*[^\n]+','[PERSON]',text)
    text=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[EMAIL]',text)
    text=re.sub(r'(?i)\b\d{5,22}@(s\.whatsapp\.net|lid|g\.us)\b','[IDENTIFIER]',text)
    text=re.sub(r'(?<!\w)(?:\+?\d[\d ()-]{5,}\d)(?!\w)','[NUMBER]',text)
    text=re.sub(r'(?i)\b(?:\d{1,3}\.){3}\d{1,3}\b','[IP]',text)
    text=re.sub(r'(?im)(?:адрес|address|ünvan|паспорт|passport|iban|voen|vöen|инн)\s*[:=—-]\s*[^\n]+','[PERSONAL DATA]',text)
    text=re.sub(r'(?i)(?:[A-Z]:\\(?:Users|Documents and Settings)\\[^\s]+|/(?:home|Users)/[^\s]+)','[PRIVATE PATH]',text)
    # Unknown free-text full names: deliberately over-redact capitalized pairs.
    text=re.sub(r'\b[A-ZА-ЯƏÖÜĞŞÇİ][a-zа-яəöüğşçı]+(?:[ \t]+[A-ZА-ЯƏÖÜĞŞÇİ][a-zа-яəöüğşçı]+){1,2}\b','[PERSON]',text)
    return text


def text(value,limit,required=False):
    if not isinstance(value,str) or len(value)>limit: raise TenantLinearError('Некорректное поле карточки.')
    value=redact(value).strip()
    if required and not value: raise TenantLinearError('Заполните обязательные поля карточки.')
    return value


def card(data,allowed_ids):
    allowed_order=list(allowed_ids);allowed_set=set(allowed_order)
    if not isinstance(data,dict) or not BASE_FIELDS<=set(data) or set(data)-FIELDS: raise TenantLinearError('Некорректная структура карточки.')
    method=data.get('resolution_method','unknown');kind=data.get('case_kind','technical')
    actions=data.get('operator_actions',[])
    if method not in {'chat','remote','phone','unknown'} or kind not in {'technical','informational'}: raise TenantLinearError('Некорректный тип кейса или способ решения.')
    if not isinstance(actions,list) or len(actions)>10: raise TenantLinearError('Некорректные действия сотрудника.')
    content_language=data.get('content_language','az')
    if not isinstance(content_language,str) or content_language not in {'ru','az','en'}: raise TenantLinearError('Язык карточки: ru, az или en.')
    clarification_note=data.get('clarification_note','')
    work_status=data.get('work_status','unknown')
    case_relation=data.get('case_relation','unknown')
    if case_relation not in {'new_request','continuation','recurrence','new_issue','unknown'}: raise TenantLinearError('Некорректный тип связи с обращением.')
    if work_status not in {'resolved','unresolved','no_response','awaiting_information','in_progress','unknown'}: raise TenantLinearError('Некорректный результат обращения.')
    if not isinstance(clarification_note,str): raise TenantLinearError('Комментарий уточнения должен быть текстом.')
    result={'resolution_method':method,'case_kind':kind,'operator_actions':[text(v,800,True) for v in actions],'content_language':content_language,
      'clarification_note':text(clarification_note,2000),'work_status':work_status,'case_relation':case_relation}
    result.update({key:text(data[key],180 if key=='title' else 120, key=='title') for key in ('title','product','product_version')})
    for key in ('symptoms','resolution_steps'):
        values=data[key]
        if not isinstance(values,list) or not (1 if key=='symptoms' else 0)<=len(values)<=10: raise TenantLinearError('Нужны симптомы и шаги решения.')
        result[key]=[text(v,800,True) for v in values]
    cause=data['cause'];outcome=data['outcome']
    if not isinstance(cause,dict) or set(cause)!={'status','text'} or cause['status'] not in ('confirmed','suspected','unknown'): raise TenantLinearError('Некорректная причина.')
    if not isinstance(outcome,dict) or set(outcome)!={'status','text','message_ids'} or outcome['status'] not in ('confirmed','unconfirmed','unknown'): raise TenantLinearError('Некорректное подтверждение.')
    result['cause']={'status':cause['status'],'text':text(cause['text'],1200)}
    def ids(values):
        if not isinstance(values,list) or len(values)>MAX_MESSAGES: raise TenantLinearError('Некорректные ссылки на сообщения.')
        normalized=list(dict.fromkeys(identifier(v) for v in values))
        if set(normalized)-allowed_set: raise TenantLinearError('Сообщение недоступно в выбранном фрагменте.',403)
        return normalized
    result['source_message_ids']=ids(data['source_message_ids'])
    if not result['source_message_ids']: raise TenantLinearError('Укажите исходные сообщения.')
    cause_evidence=data.get('cause_evidence',[])
    if not isinstance(cause_evidence,list) or len(cause_evidence)>3:
        raise TenantLinearError('Некорректные подтверждения причины.')
    normalized_cause_evidence=[]
    for item in cause_evidence:
        if not isinstance(item,dict) or set(item)!={'message_id','quote'}:
            raise TenantLinearError('Некорректные подтверждения причины.')
        message_id=identifier(item['message_id'])
        if message_id not in result['source_message_ids']:
            raise TenantLinearError('Подтверждение причины вне исходных сообщений.',403)
        normalized_cause_evidence.append({'message_id':message_id,'quote':text(item['quote'],800,True)})
    result['cause_evidence']=normalized_cause_evidence
    start=identifier(data.get('start_message_id',result['source_message_ids'][0]));end=identifier(data.get('end_message_id',result['source_message_ids'][-1]))
    if start not in result['source_message_ids'] or end not in result['source_message_ids']: raise TenantLinearError('Границы обращения должны входить в исходные сообщения.',403)
    if allowed_order.index(start)>allowed_order.index(end): raise TenantLinearError('Начальное сообщение должно предшествовать конечному.')
    result['start_message_id']=start;result['end_message_id']=end
    evidence=data.get('operator_action_evidence',[])
    if not isinstance(evidence,list) or len(evidence)>10: raise TenantLinearError('Некорректные подтверждения действий сотрудника.')
    normalized_evidence=[]
    for item in evidence:
        if not isinstance(item,dict) or set(item)!={'action_index','message_id','quote'} or type(item['action_index']) is not int or not 0<=item['action_index']<len(result['operator_actions']):
            raise TenantLinearError('Некорректные подтверждения действий сотрудника.')
        message_id=identifier(item['message_id'])
        if message_id not in result['source_message_ids']: raise TenantLinearError('Подтверждение действия вне исходных сообщений.',403)
        normalized_evidence.append({'action_index':item['action_index'],'message_id':message_id,'quote':text(item['quote'],800,True)})
    result['operator_action_evidence']=normalized_evidence
    result['outcome']={'status':outcome['status'],'text':text(outcome['text'],1200),'message_ids':ids(outcome['message_ids'])}
    if set(result['outcome']['message_ids'])-set(result['source_message_ids']): raise TenantLinearError('Подтверждение должно входить в исходные сообщения.')
    if result['outcome']['status']=='confirmed' and not result['outcome']['message_ids']: raise TenantLinearError('Нужна ссылка на подтверждающее сообщение.')
    if result['outcome']['status']=='confirmed' and not result['resolution_steps'] and kind!='informational' and method not in {'remote','phone'}:
        raise TenantLinearError('Подтверждённое решение требует шагов.')
    return result


def positive_confirmation(value,informational=False):
    if '?' in value or re.search(r'\b(?:не|нет|not|never|yox|işləmir|если|возможно|кажется|попробуйте|проверьте|if|check|maybe|should|might|бы|должно|может|наверное)\b',value,re.I): return False
    if informational and re.search(r'понятно|спасибо.{0,40}информац|thank.{0,20}information|aydındır|məlumat.{0,40}təşəkkür',value,re.I): return True
    return bool(re.search(r'(?:исправлен[оа]?|həll olundu|aradan qaldırıldı|заработал[оа]?|вс[её] работает|теперь работает|проблема решена|помогло|it works|working now|\bresolved\b|\bfixed\b|\bdüzəldi\b|\bişləyir\b)',value,re.I))



def moment(value):
    from datetime import datetime
    if not isinstance(value,str): value=str(value)
    try: parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError: raise TenantLinearError('Период должен быть RFC3339 с часовым поясом.') from None
    if parsed.tzinfo is None: raise TenantLinearError('Укажите часовой пояс периода.')
    return parsed


def period(data,required=False):
    from datetime import timedelta,timezone
    if not required and not data.get('period_start') and not data.get('period_end'): return None,None
    start,end=moment(data.get('period_start')),moment(data.get('period_end'))
    if not start<end or end-start>timedelta(days=31): raise TenantLinearError('Период должен быть от 1 секунды до 31 дня.')
    return start.astimezone(timezone.utc).isoformat(),end.astimezone(timezone.utc).isoformat()


def approval_reason(value):
    if value['outcome']['status']!='confirmed': return 'confirmation_required'
    if value.get('case_kind','technical')=='technical' and value.get('work_status','unknown')!='resolved': return 'work_status_required' if value.get('work_status','unknown')=='unknown' else 'work_not_resolved'
    if value.get('resolution_method') in {'remote','phone'} and not value.get('operator_actions'): return 'operator_actions_required'
    if value.get('case_kind','technical')=='technical' and not value['resolution_steps']: return 'solution_required'
    if value.get('cause',{}).get('status')=='confirmed' and not value.get('cause_evidence'): return 'cause_evidence_required'
    return None
