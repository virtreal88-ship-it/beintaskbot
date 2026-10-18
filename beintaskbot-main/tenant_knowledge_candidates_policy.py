"""Bounded extraction, conservative privacy redaction and strict review cards."""
import re
from tenant_linear_policy import TenantLinearError, identifier
MAX_MESSAGES=80
MAX_CONTEXT=12000
MAX_TOKENS=16000
MAX_CARDS=5
FIELDS={'title','symptoms','cause','resolution_steps','outcome','source_message_ids','product','product_version'}


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
    if not isinstance(data,dict) or set(data)!=FIELDS: raise TenantLinearError('Некорректная структура карточки.')
    result={key:text(data[key],180 if key=='title' else 120, key=='title') for key in ('title','product','product_version')}
    for key in ('symptoms','resolution_steps'):
        values=data[key]
        if not isinstance(values,list) or not 1<=len(values)<=10: raise TenantLinearError('Нужны симптомы и шаги решения.')
        result[key]=[text(v,800,True) for v in values]
    cause=data['cause'];outcome=data['outcome']
    if not isinstance(cause,dict) or set(cause)!={'status','text'} or cause['status'] not in ('confirmed','suspected','unknown'): raise TenantLinearError('Некорректная причина.')
    if not isinstance(outcome,dict) or set(outcome)!={'status','text','message_ids'} or outcome['status'] not in ('confirmed','unconfirmed','unknown'): raise TenantLinearError('Некорректное подтверждение.')
    result['cause']={'status':cause['status'],'text':text(cause['text'],1200)}
    def ids(values):
        if not isinstance(values,list) or len(values)>MAX_MESSAGES: raise TenantLinearError('Некорректные ссылки на сообщения.')
        normalized=list(dict.fromkeys(identifier(v) for v in values))
        if set(normalized)-set(allowed_ids): raise TenantLinearError('Сообщение недоступно в выбранном фрагменте.',403)
        return normalized
    result['source_message_ids']=ids(data['source_message_ids'])
    if not result['source_message_ids']: raise TenantLinearError('Укажите исходные сообщения.')
    result['outcome']={'status':outcome['status'],'text':text(outcome['text'],1200),'message_ids':ids(outcome['message_ids'])}
    if set(result['outcome']['message_ids'])-set(result['source_message_ids']): raise TenantLinearError('Подтверждение должно входить в исходные сообщения.')
    if result['outcome']['status']=='confirmed' and not result['outcome']['message_ids']: raise TenantLinearError('Нужна ссылка на подтверждающее сообщение.')
    return result


def positive_confirmation(value):
    if '?' in value or re.search(r'\b(?:не|нет|not|never|yox|işləmir|если|возможно|кажется|попробуйте|проверьте|if|check|maybe|should|might|бы|должно|может|наверное)\b',value,re.I): return False
    return bool(re.search(r'(?:заработал[оа]?|вс[её] работает|теперь работает|проблема решена|помогло|it works|working now|\bresolved\b|\bfixed\b|\bdüzəldi\b|\bişləyir\b)',value,re.I))



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
