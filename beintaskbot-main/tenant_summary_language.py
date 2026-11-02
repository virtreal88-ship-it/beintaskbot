"""Company summary language, independent of interface locale and reply policy.

Language detection is local and bounded to the already selected customer
evidence. It never fetches media, transcribes, invokes AI, or expands a case.
"""
import re
import unicodedata

from tenant_linear_policy import TenantLinearError, identifier
from tenant_platform import _connect, _json

LANGUAGES = {'az': 'Azerbaijani', 'ru': 'Russian', 'en': 'English', 'tr': 'Turkish'}
POLICY_KEY = 'summary_language'
DETECTOR_VERSION = 1


def language(value, *, optional=False):
    if optional and value in (None, '', 'auto'):
        return None
    if value not in LANGUAGES:
        raise TenantLinearError('Язык сводки: AZ, RU, EN или TR.', 400)
    return value


def read_policy(cur, tenant):
    cur.execute('SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key=%s',
                (tenant, POLICY_KEY))
    stored = cur.fetchone()
    if stored:
        value = stored['value']
        if not isinstance(value, dict) or value.get('mode') not in {'automatic', 'fixed'}:
            raise TenantLinearError('Проверьте настройку языка сводок компании.', 409)
        return {**value, 'source': 'company'}
    # Preserve the actual earlier per-consumer policy for existing companies.
    # This explicit compatibility rule never rewrites or regenerates history.
    return {'mode': 'legacy',
            'fallback_language': 'az', 'fixed_language': None, 'version': 0,
            'source': 'legacy'}


def _owner(cur, profile):
    if not isinstance(profile, dict):
        raise TenantLinearError('Нет доступа.', 403)
    tenant = identifier(profile.get('tenant_id'))
    try:
        actor = int(profile.get('telegram_id'))
    except (TypeError, ValueError):
        raise TenantLinearError('Нет доступа.', 403) from None
    cur.execute('''SELECT 1 FROM saas_tenants t JOIN saas_tenant_members m ON m.tenant_id=t.id
      WHERE t.id=%s::uuid AND t.owner_telegram_id=%s AND m.telegram_id=%s
        AND m.role='owner' AND m.active=TRUE AND t.status IN ('active','onboarding','ready_for_integration')
      FOR SHARE OF t,m''', (tenant, actor, actor))
    if not cur.fetchone():
        raise TenantLinearError('Настройка языка сводок доступна владельцу компании.', 403)
    return tenant, actor


def read(profile):
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant, _ = _owner(cur, profile)
            return read_policy(cur, tenant)


def save(profile, data):
    if not isinstance(data, dict) or set(data) != {'expected_version', 'mode', 'fallback_language', 'fixed_language'}:
        raise TenantLinearError('Некорректная настройка языка сводок.', 400)
    if type(data['expected_version']) is not int or data['expected_version'] < 0:
        raise TenantLinearError('Обновите версию настройки языка сводок.', 409)
    if data['mode'] not in {'automatic', 'fixed'}:
        raise TenantLinearError('Выберите правило языка сводок.', 400)
    fallback = language(data['fallback_language'])
    fixed = language(data['fixed_language']) if data['mode'] == 'fixed' else None
    if data['mode'] == 'automatic' and data['fixed_language'] is not None:
        raise TenantLinearError('В автоматическом режиме выберите запасной язык.', 400)
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant, actor = _owner(cur, profile)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('summary-language:' + tenant,))
            previous = read_policy(cur, tenant)
            if previous['version'] != data['expected_version']:
                raise TenantLinearError('Настройка изменена другим сотрудником. Обновите её перед сохранением.', 409)
            value = {'mode': data['mode'], 'fallback_language': fallback,
                     'fixed_language': fixed, 'version': previous['version'] + 1}
            cur.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by)
              VALUES(%s::uuid,%s,%s::jsonb,%s) ON CONFLICT(tenant_id,policy_key) DO UPDATE
              SET value=EXCLUDED.value,updated_by=EXCLUDED.updated_by,updated_at=now()''',
                        (tenant, POLICY_KEY, _json(value), actor))
            from tenant_ai_agents_store import _audit
            _audit(cur, tenant, actor, 'summary_language_policy_changed', 'company', tenant,
                   {'previous': previous, 'current': value, 'history_regenerated': False})
        conn.commit()
    return {**value, 'source': 'company'}


def _plain(value):
    text = str(value or '').lower().replace('ə', 'e').replace('ı', 'i')
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))


def customer_evidence(cur, tenant, rows, channel_id, conversation_id):
    """Map stored route evidence; unidentified group speakers cannot vote.

    The existing server-owned staff registry, rather than display names or a
    caller-supplied role, identifies employee participation in a group.
    """
    group = str(conversation_id).endswith('@g.us')
    staff = set()
    if group:
        channels=sorted({str(source.get('channel_id')) for row in rows for source in row.get('sources') or []
                         if source.get('channel_id')}|{str(row.get('source_channel_id') or channel_id) for row in rows})
        cur.execute('SELECT channel_id::text AS channel_id,jid FROM saas_ai_group_staff WHERE tenant_id=%s::uuid AND channel_id::text=ANY(%s::text[])',
                    (tenant, channels))
        staff = {(str(row['channel_id']),str(row['jid'])) for row in cur.fetchall()}
    employees={}
    result = []
    for row in rows:
        raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
        role = 'customer' if row.get('direction') == 'incoming' else 'operator'
        if raw.get('ai_authored') or raw.get('run_id') or row.get('author_name') == 'AI Agent':
            role = 'ai'
        elif raw.get('verified_employee_id') is not None or raw.get('fromMe') is True:
            role = 'operator'
        elif group:
            from tenant_operator_dialogs import _participant_fields
            from tenant_ai_group_coordination import verified_employee
            participant,_,_ = _participant_fields(raw)
            if not isinstance(participant, str) or not re.fullmatch(r'\d{5,22}@(s\.whatsapp\.net|c\.us|lid)', participant):
                role = 'system'
            else:
                channels={str(row.get('source_channel_id') or channel_id)}|{str(source['channel_id'])
                    for source in row.get('sources') or [] if source.get('channel_id')}
                for source_channel in sorted(channels):
                    key=(source_channel,participant)
                    if key not in employees:
                        employees[key]=verified_employee(cur,tenant,{'participantJid':participant,'channelId':source_channel})
                    if key in staff or employees[key] is not None:
                        role='operator';break
        result.append({**row, 'role': role})
    return result


_WORDS = {
    'az': set('salam zehmet olmasa proqram proqramda mehsul mehsulu duzelmir duzelib islemir xeta yoxdur var satis alis qaimeni hesab yuklenmir acilmir gonderin gonderdim yoxlayin komek lazim edir ede edirem edirik bilmirik olmur olunmur deyil niye nece qiymet melumat mushteri musteri musterinin hell edin xahis bugun sabah qalir gorunmur yazilir yaziram silinmir duzeltmek meseleni'.split()),
    'tr': set('merhaba lutfen program programda urun urunu duzelmiyor calismiyor hata yok var satis alis faturayi hesap yuklenmiyor acilmiyor gonderin gonderdim kontrol yardim gerekiyor eder misiniz yapamiyorum olmuyor degil neden nasil fiyat bilgi musteri cozum bugun yarin gorunmuyor yaziyor silemiyorum kaydedemiyorum sorun sorunu'.split()),
    'en': set('hello please the this that is are was were have has cannot can not with when why how our my your we you after before save saving delete deleted customer product invoice payment amount error issue problem works working stopped fix help need want shows showing open upload fails failed message request'.split()),
}
_REQUESTS = {
    'az': r'(?:на\s+азербайджанском|in\s+azerbaijani|azerbaycan(?:ca|\s+dilinde))',
    'ru': r'(?:на\s+русском|in\s+russian|rus(?:ca|\s+dilinde))',
    'en': r'(?:на\s+английском|in\s+english|ingilis(?:ce|\s+dilinde)|ingilizce)',
    'tr': r'(?:на\s+турецком|in\s+turkish|turk(?:ce|\s+dilinde))',
}
_ASK = r'(?:ответ|пиш|говор|пожалуйста|respond|reply|answer|write|speak|please|cavab|yaz|danis|lutfen|zehmet|konus)'


def _customer_text(message):
    raw = message.get('raw') if isinstance(message.get('raw'), dict) else {}
    role = message.get('summary_language_role',message.get('role'))
    if (not (role == 'customer' or message.get('direction') == 'incoming')
            or role not in (None, 'customer') or message.get('direction') not in (None, 'incoming')
            or raw.get('verified_employee_id') is not None or raw.get('ai_authored') is True
            or raw.get('run_id') or message.get('author_name') == 'AI Agent'):
        return ''
    if 'customer_language_text' in message:
        value=str(message['customer_language_text'] or '')+'\n'+str(message.get('customer_language_transcript') or '')
    elif message.get('message_type') not in (None, 'text'):
        # Only already verified reusable evidence may supply media text.
        value = message.get('transcript') or message.get('_copilot_attachment_text') or ''
        saved = message.get('media_evidence') or {}
        if (not value and saved.get('status') == 'processed' and saved.get('has_audio_result') is True
                and (saved.get('coverage') or {}).get('complete') is True):
            context = message.get('media_evidence_context') or ''
            match = re.search(r'\[Saved media transcript\]\s*(.*?)(?=\n\[|\Z)', context, re.S)
            if match:
                value = match.group(1)
    else:
        value = message.get('text', message.get('body', ''))
    value = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+|\b\w*\d\w*\b', ' ', str(value or ''))
    return value[:6000]


_ACKNOWLEDGEMENTS = set('спасибо благодарю благодарим большое огромное очень всем вам вас вашу за помощь помогли помогло все работает теперь хорошо да нет ок ага thank thanks you your for help very much so everyone works worked now great okay ok yes no cox chox sag olun sagol sagolun tesekkur tesekkurler yaxsi oldu aydindir evet hayir cok'.split())
_TECHNICAL=re.compile(r'программ|ошиб|обновл|слома|сохраня|открыва|подключ|сбой|save|fail|error|acilmiyor|calismiyor|silinmir|islemir|duzelmir|xeta')


def _scores(text):
    tokens = re.findall(r'[^\W\d_]+', _plain(text), re.UNICODE)
    if len(tokens) < 2 or sum(map(len, tokens)) < (8 if len(tokens)==2 else 12):
        return None
    if all(token in _ACKNOWLEDGEMENTS for token in tokens):
        return None
    if len(tokens)==2 and not _TECHNICAL.search(' '.join(tokens)):
        return None
    if sum(len(token) for token in tokens if re.search('[а-яё]', token)) >= (8 if len(tokens)==2 else 10):
        return 'ru', min(30, len(tokens)), len(tokens)
    scores = {code: sum(token in words for token in tokens) for code, words in _WORDS.items()}
    # Shared words cannot make diacritic-free Azerbaijani into Turkish.
    distinct = {code: sum(token in (_WORDS[code] - _WORDS['az' if code == 'tr' else 'tr']) for token in tokens)
                for code in ('az', 'tr')}
    if distinct['tr'] < 2:
        scores['tr'] = 0
    if scores['az'] == scores['tr'] and scores['az']:
        scores['tr'] = 0
    winner = max(scores, key=scores.get)
    ordered = sorted(scores.values(), reverse=True)
    return (winner, scores[winner], len(tokens)) if scores[winner] >= 2 and ordered[0] > ordered[1] else None


def resolve(policy, messages, *, explicit=None, legacy_language='az'):
    """Return immutable generation provenance; the caller persists it with text."""
    explicit = language(explicit, optional=True)
    selected = {'policy_version': int(policy.get('version', 0)), 'policy_source': policy.get('source', 'company'),
                'detector_version': DETECTOR_VERSION}
    if explicit:
        return {**selected, 'language': explicit, 'method': 'explicit'}
    if policy.get('mode') == 'fixed':
        return {**selected, 'language': language(policy.get('fixed_language')), 'method': 'company_fixed'}
    if policy.get('mode') == 'legacy' and legacy_language in LANGUAGES:
        return {**selected, 'language': legacy_language, 'method': 'legacy'}
    found = []
    requested = None
    for message in messages[-80:]:
        text = _customer_text(message)
        plain = _plain(text)
        if re.search(_ASK, plain):
            matches = [(match.start(), code) for code, pattern in _REQUESTS.items() for match in re.finditer(pattern, plain)]
            if matches:
                requested = max(matches)[1]
        score = _scores(text)
        if score:
            found.append(score)
    if requested:
        return {**selected, 'language': requested, 'method': 'customer_request'}
    if found:
        # A sustained final phase wins; an isolated short acknowledgement
        # never enters this list. Otherwise use the customer's dominant text.
        if len(found) >= 2 and found[-1][0] == found[-2][0]:
            code = found[-1][0]
        else:
            totals = {code: sum(min(30, score) for detected, score, _size in found if detected == code) for code in LANGUAGES}
            ranked = sorted(totals, key=totals.get, reverse=True)
            code = ranked[0] if totals[ranked[0]] > totals[ranked[1]] else None
        if code:
            return {**selected, 'language': code, 'method': 'customer_detected'}
    return {**selected, 'language': language(policy.get('fallback_language', 'az')), 'method': 'fallback'}


def instructions(selection):
    return ('\nWrite all summary/explanatory fields in ' + LANGUAGES[selection['language']] +
            '. This rule controls only summary content, independently of interface locale, agent reply language, '
            'and the developer response language. Preserve exact source quotes, names, product labels, numbers, '
            'amounts, barcodes, commands and error text. Never invent missing media contents.\n')
