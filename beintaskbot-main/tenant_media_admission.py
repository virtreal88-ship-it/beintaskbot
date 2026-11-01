"""Fixed public reasons for a media call rejected before provider dispatch."""
from tenant_linear_policy import TenantLinearError

REASONS = {
    'ai_spend_vision_price_unknown': ('Не настроен тариф анализа изображения для выбранной модели.', 'Seçilmiş model üçün şəkil təhlili tarifi qurulmayıb.', 'The selected model has no configured image-analysis tariff.'),
    'vision_source_access_denied': ('Нет доступа к источнику изображения.', 'Şəkil mənbəyinə giriş yoxdur.', 'Access to the image source was denied.'),
    'vision_admission_failed': ('Анализ изображения не допущен. Проверьте настройки AI.', 'Şəkil təhlilinə icazə verilmədi. AI ayarlarını yoxlayın.', 'Image analysis admission failed. Check AI settings.'),
    'ai_spend_media_price_unknown': ('Не настроен тариф ASR для выбранной модели.', 'Seçilmiş model üçün ASR tarifi qurulmayıb.', 'The selected model has no configured ASR tariff.'),
    'ai_spend_budget_exhausted': ('Исчерпан денежный лимит AI.', 'AI pul limiti tükənib.', 'The AI spending limit is exhausted.'),
    'ai_call_limit_exhausted': ('Исчерпан дневной лимит вызовов AI.', 'Gündəlik AI sorğu limiti tükənib.', 'The daily AI call limit is exhausted.'),
    'media_authority_changed': ('Сообщение, назначение агента или настройки изменились.', 'Mesaj, agent təyinatı və ya ayarlar dəyişib.', 'The message, agent assignment or settings changed.'),
    'source_access_denied': ('Нет доступа к источнику аудио.', 'Səs mənbəyinə giriş yoxdur.', 'Access to the audio source was denied.'),
    'media_admission_failed': ('Вызов ASR не допущен. Проверьте настройки AI.', 'ASR sorğusuna icazə verilmədi. AI ayarlarını yoxlayın.', 'ASR admission failed. Check AI settings.'),
}


def mark(error, step, operation='asr'):
    """Only our pre-dispatch boundary may certify admission; never raw text."""
    if not isinstance(error, TenantLinearError):
        return
    code = 'media_admission_failed'
    if step == 'budget' and (str(error), error.status) in {
            ('ai_spend_media_price_unknown', 409), ('ai_spend_budget_exhausted', 429)}:
        code = str(error)
    elif error.status == 403:
        code = 'source_access_denied'
    elif step == 'authority' and error.status == 409:
        code = 'media_authority_changed'
    elif step == 'calls' and error.status == 429:
        code = 'ai_call_limit_exhausted'
    if operation=='vision_low':
        code={'ai_spend_media_price_unknown':'ai_spend_vision_price_unknown',
              'source_access_denied':'vision_source_access_denied',
              'media_admission_failed':'vision_admission_failed'}.get(code,code)
    error.media_admission_code = code


def explanation(code, language):
    values = REASONS.get(code)
    return values[{'ru': 0, 'az': 1, 'en': 2}.get(language, 0)] if values else ''
