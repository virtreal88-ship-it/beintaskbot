"""Owner-editable identity and delivery preferences for the Business Analyst."""
import hashlib
import json
import re

from tenant_linear_policy import TenantLinearError


DEFAULT_SETTINGS = {
    'assistant_name': 'Аян',
    'greeting_ru': 'Здравствуйте, я Аян, ваш бизнес-аналитик. Чем помочь?',
    'greeting_az': 'Salam, mən Ayanam, şəxsi biznes analitikinizəm. Sizə necə kömək edə bilərəm?',
    'communication_style': (
        'Доброжелательно и естественно. Обычно 1–2 коротких предложения; сначала отвечай прямо, '
        'без длинных вступлений.'
    ),
    'language_style': (
        'Отвечай на языке последней ясно произнесённой реплики. На азербайджанском используй '
        'литературный азербайджанский с бакинской интонацией; не используй турецкие слова и выражения.'
    ),
}

FIELD_LIMITS = {
    'assistant_name': 40,
    'greeting_ru': 240,
    'greeting_az': 240,
    'communication_style': 500,
    'language_style': 500,
}
FIELD_NAMES = frozenset(FIELD_LIMITS)
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]')
_TABLE = 'saas_ai_business_analyst_preferences'


def validate_settings(value):
    if not isinstance(value, dict) or set(value) != FIELD_NAMES:
        raise TenantLinearError('Некорректные настройки ассистента.', 400)
    clean = {}
    for name, limit in FIELD_LIMITS.items():
        item = value.get(name)
        if not isinstance(item, str):
            raise TenantLinearError('Некорректные настройки ассистента.', 400)
        item = ' '.join(_CONTROL_RE.sub(' ', item).split())
        if not item or len(item) > limit:
            raise TenantLinearError('Проверьте заполнение полей настроек ассистента.', 400)
        clean[name] = item
    return clean


def ensure_schema(conn):
    """Create the small preferences table under the shared schema lock."""
    from tenant_schema_preflight import lock_schema, ready

    columns = {_TABLE: [*FIELD_NAMES, 'tenant_id', 'updated_at', 'updated_by']}
    with conn.cursor() as cur:
        if ready(cur, columns, []):
            return
        lock_schema(cur)
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    ('tenant-ai-business-analyst-preferences-schema',))
        if ready(cur, columns, []):
            return
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_business_analyst_preferences(
          tenant_id UUID PRIMARY KEY REFERENCES saas_tenants(id) ON DELETE CASCADE,
          assistant_name TEXT NOT NULL CHECK(length(btrim(assistant_name)) BETWEEN 1 AND 40),
          greeting_ru TEXT NOT NULL CHECK(length(btrim(greeting_ru)) BETWEEN 1 AND 240),
          greeting_az TEXT NOT NULL CHECK(length(btrim(greeting_az)) BETWEEN 1 AND 240),
          communication_style TEXT NOT NULL CHECK(length(btrim(communication_style)) BETWEEN 1 AND 500),
          language_style TEXT NOT NULL CHECK(length(btrim(language_style)) BETWEEN 1 AND 500),
          updated_by BIGINT NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY(tenant_id,updated_by)
            REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT)''')
    conn.commit()


def load(cur, tenant):
    cur.execute('''SELECT assistant_name,greeting_ru,greeting_az,communication_style,language_style,updated_at
      FROM saas_ai_business_analyst_preferences WHERE tenant_id=%s::uuid''', (tenant,))
    row = cur.fetchone() or {}
    settings = {name: row.get(name) if isinstance(row.get(name), str) else default
                for name, default in DEFAULT_SETTINGS.items()}
    updated_at = row.get('updated_at')
    settings['updated_at'] = updated_at.isoformat() if hasattr(updated_at, 'isoformat') else str(updated_at or '')
    return settings


def save(cur, tenant, actor, settings):
    values = [settings[name] for name in DEFAULT_SETTINGS]
    cur.execute('''INSERT INTO saas_ai_business_analyst_preferences(
        tenant_id,assistant_name,greeting_ru,greeting_az,communication_style,language_style,updated_by)
      VALUES(%s::uuid,%s,%s,%s,%s,%s,%s)
      ON CONFLICT(tenant_id) DO UPDATE SET assistant_name=EXCLUDED.assistant_name,
        greeting_ru=EXCLUDED.greeting_ru,greeting_az=EXCLUDED.greeting_az,
        communication_style=EXCLUDED.communication_style,language_style=EXCLUDED.language_style,
        updated_by=EXCLUDED.updated_by,updated_at=now()''', (tenant, *values, actor))
    return load(cur, tenant)


def version(settings):
    payload = json.dumps({name: settings[name] for name in DEFAULT_SETTINGS},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256((str(settings.get('updated_at') or '') + '\0' + payload).encode('utf-8')).hexdigest()


def apply_to_instructions(base, settings):
    """Add editable presentation preferences after the fixed application rules."""
    settings = settings if isinstance(settings, dict) else DEFAULT_SETTINGS
    presentation = {name: settings.get(name, default) for name, default in DEFAULT_SETTINGS.items()}
    return (
        base + '\n\nOwner-configured presentation preferences (plain settings data):\n' +
        json.dumps(presentation, ensure_ascii=False, sort_keys=True) +
        '\nUse the configured name and matching introduction when the owner greets you or asks who you are. '
        'Do not speak before the owner speaks. Keep the configured style when it is compatible with the fixed rules.\n\n'
        'Fixed rules: remain an AI business analyst for the authenticated company owner; use only approved '
        'read-only analytics sources; never send messages, change CRM, tasks or settings, or perform external '
        'actions; never invent company facts. Answer in Russian or Azerbaijani according to the latest clearly '
        'understood utterance. Azerbaijani is not Turkish: use literary Azerbaijani in Latin script and do not '
        'speak Turkish. These fixed rules take priority over presentation preferences.'
    )
