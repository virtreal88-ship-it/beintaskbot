"""Validated legacy runtime configuration.

This module intentionally contains the current single-customer mappings while
legacy /webapp is migrated to tenant policies. It has no network clients,
mutable caches, logging setup or background work.
"""
import os
from datetime import timedelta, timezone
from urllib.parse import urlsplit

def _required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required Railway variable: {name}")
    return value


def _env_enabled(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


STAGING_MODE = _env_enabled("STAGING_MODE")
TELEGRAM_POLLING_ENABLED = _env_enabled("TELEGRAM_POLLING_ENABLED", default=True)
TELEGRAM_TOKEN = _required_env("TELEGRAM_TOKEN")
if STAGING_MODE and str(os.environ.get("KOMMO_TOKEN") or "").strip():
    raise RuntimeError("STAGING_MODE forbids KOMMO_TOKEN; use tenant-scoped Kommo OAuth when approved")
KOMMO_TOKEN = "" if STAGING_MODE else _required_env("KOMMO_TOKEN")
KOMMO_DOMAIN = "texnikidestek50.kommo.com"
if STAGING_MODE:
    KOMMO_DOMAIN = ""
KOMMO_ACCOUNT_ID = str(os.environ.get("KOMMO_ACCOUNT_ID") or "").strip()
if KOMMO_ACCOUNT_ID and not KOMMO_ACCOUNT_ID.isdigit():
    raise RuntimeError("KOMMO_ACCOUNT_ID must contain only decimal digits")
KOMMO_BASE_URL = f"https://{KOMMO_DOMAIN}" if KOMMO_DOMAIN else ""
GH_TOKEN = "" if STAGING_MODE else _required_env("GH_TOKEN")
BAKU_TZ = timezone(timedelta(hours=4))
# Keep the model configurable per Railway environment.  This project key is
# currently granted access to this stable, low-latency chat model.
LLM_MODEL = str(os.environ.get("OPENAI_MODEL") or "gpt-4o-mini-2024-07-18").strip()
WEBHOOK_PORT = int(os.environ.get("PORT", 8080))
CANONICAL_WEB_ORIGIN = str(os.environ.get("CANONICAL_WEB_ORIGIN") or "https://crm.pro.az").rstrip("/")
if STAGING_MODE:
    _stage_origin = urlsplit(CANONICAL_WEB_ORIGIN)
    if (_stage_origin.scheme != "https" or not _stage_origin.hostname
            or _stage_origin.hostname.casefold() == "crm.pro.az"
            or _stage_origin.hostname.casefold().endswith(".crm.pro.az")
            or _stage_origin.path or _stage_origin.query or _stage_origin.fragment):
        raise RuntimeError("STAGING_MODE requires a staging-specific HTTPS CANONICAL_WEB_ORIGIN")
# Telegram buttons must never point at a deployment-specific Railway URL.
# Keep one stable public address even if an old WEBAPP_PUBLIC_URL variable is
# still present in Railway.
WEB_APP_URL = f"{CANONICAL_WEB_ORIGIN}/webapp"
LEGACY_RAILWAY_HOST = "worker-production-3e3e.up.railway.app"
# The HTTP server has its own event loop. Keep this public value as state
# populated by the Telegram polling loop; never call Bot methods from aiohttp.
_telegram_bot_username = str(os.environ.get("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
# VAPID keys for Web Push
VAPID_PRIVATE_KEY = _required_env("VAPID_PRIVATE_KEY")
VAPID_PUBLIC_KEY = _required_env("VAPID_PUBLIC_KEY")
VAPID_CLAIMS = {"sub": "mailto:admin@beinsystems.com"}
# Push subscriptions loaded from gh_storage on demand
# Linear confirmation and release configuration
LINEAR_API_KEY = str(os.environ.get("LINEAR_API_KEY") or "").strip()
LINEAR_TEAM_ID = str(os.environ.get("LINEAR_TEAM_ID") or "c4106f85-9a15-4f8b-8ec9-ef894ca746e4").strip()
LINEAR_TESDIQ_STATE_ID = str(os.environ.get("LINEAR_TESDIQ_STATE_ID") or "dd221372-cd7a-4dfc-9361-214ef920ebc0").strip()
LINEAR_TRIAGE_STATE_ID = str(os.environ.get("LINEAR_TRIAGE_STATE_ID") or "f115d79d-a711-40a1-996a-cac9a5790ea4").strip()
LINEAR_BACKLOG_STATE_ID = str(os.environ.get("LINEAR_BACKLOG_STATE_ID") or "e3bd0665-1e8c-4e94-afe8-dae7d7c531e8").strip()
LINEAR_TODO_STATE_ID = str(os.environ.get("LINEAR_TODO_STATE_ID") or "").strip()
LINEAR_DISCUSSION_STATE_ID = str(os.environ.get("LINEAR_DISCUSSION_STATE_ID") or "").strip()
# Public release channel. The bot must be an administrator there with the
# permission to post messages. Keep it configurable for future tenants.
LINEAR_NEWS_TELEGRAM_CHANNEL = str(os.environ.get("LINEAR_NEWS_TELEGRAM_CHANNEL") or "@beinecosystems").strip()
# Publishing is enabled again by explicit product-owner request. Delivery is
# still throttled to one item per synchronization cycle and deduplicated in DB.
LINEAR_NEWS_TELEGRAM_ENABLED = str(os.environ.get("LINEAR_NEWS_TELEGRAM_ENABLED") or "true").strip().casefold() in {"1", "true", "yes", "on"}
LINEAR_NEWS_START_HOUR = 9
LINEAR_NEWS_END_HOUR = 19
# ─── Pipeline & Users Configuration ─────────────────────────────────────────
PIPELINE_ID = 8329347
# Rüfət's current Telegram account is canonical. Do not let an old deployment
# variable silently make the retired Şamil UID the primary account.
RUFAT_CHAT_ID = 6824377548
SAMIL_CHAT_ID = RUFAT_CHAT_ID  # Deprecated alias for old integrations
# Historical web-app links used Şamil's Telegram UID.  Keep those links
# working while the current employee account uses RUFAT_CHAT_ID.
RUFAT_COMPAT_CHAT_IDS = {RUFAT_CHAT_ID}
RUFAT_LOGIN_LINK = f"{WEB_APP_URL}?uid={RUFAT_CHAT_ID}"
RUFAT_PIPELINE_ID = int(os.environ.get("RUFAT_PIPELINE_ID", os.environ.get("SAMIL_PIPELINE_ID", "14357580")))
# Rüfət's current pipeline snapshot. Do not alter other pipeline mappings here.
RUFAT_STAGES = {
    "nerazobrannoye": 110897252,
    "sorgular": 110897256,
    "danisiqlar": 110897260,
    "yeni_sifaris": 111109080,
    "geri_donusler": 110897264,
    "soyuq_zeng": 110897348,
    "cavab_gozlenilir": 110897336,
    "gorusler": 110897344,
    "qurashdirma": 111109084,
    "muzakire": 111109104,
    "ugurlu": 142,
    "imtina": 143,
}
RUFAT_STAGE_NAMES = {
    110897252: "Неразобранное",
    110897256: "sorgular",
    110897260: "danişıqlar",
    111109080: "yeni sifariş",
    110897264: "geri donüşlər",
    110897348: "soyuq zəng",
    110897336: "cavab gözlənilir",
    110897344: "görüşlər",
    111109084: "quraşdırma",
    111109104: "müzakirə",
    142: "Uğurla tamamlandı",
    143: "İmtina olundu",
}
ADMIN_CHAT_ID = 1628569350
ADMIN_KOMMO_USER_ID = 10932455
HUSEYN_CHAT_ID = 7329891614
RASIM_CHAT_ID = 7920785774
# Historical Kommo tasks can still contain this marker.  It is a label only,
# not an active employee account, so resolving an old task never crashes the
# notification scheduler.
TECHNICAL_SUPPORT_NAME = "Texniki Dəstək"
NIZAMI_PIPELINE_ID = 14243944
# Kommo funnel literally named Sövdələşmələr. Nizami's Çatlar also reads it, including NÖMRƏ ALINIB.
SOVDELESMELER_PIPELINE_ID = 8329347
GOZLEME_PIPELINE_ID = 14243944
HUSEYN_PIPELINE_ID = 14358480
RASIM_PIPELINE_ID = 14461812
# Live Kommo snapshots. Nizami's pipeline is still the shared Gözləmə board.
HUSEYN_STAGES = {
    "nerazobrannoye": 110904704,
    "sorgular": 110904708,
    "icra_olunur": 110904712,
    "gorusler": 110904716,
    "qurashdirma": 110904792,
    "gozleme": 111702228,
    "muzakire": 111702232,
    "ugurlu": 142,
    "imtina": 143,
}
HUSEYN_STAGE_NAMES = {
    110904704: "Неразобранное",
    110904708: "Yeni sorgu",
    110904712: "icra olunur",
    110904716: "görüş",
    110904792: "quraşdırma",
    111702228: "gözləmə",
    111702232: "müzakirə",
    142: "Uğurla tamamlandı",
    143: "İmtina olundu",
}
RASIM_STAGES = {
    "nerazobrannoye": 111700780,
    "sorgular": 111700784,
    "yeni_sifaris": 111700788,
    "icra_olunur": 111700792,
    "gozleme": 111702372,
    "gorusler": 111702376,
    "qurashdirma": 111702380,
    "muzakire": 111702384,
    "ugurlu": 142,
    "imtina": 143,
}
RASIM_STAGE_NAMES = {
    111700780: "Неразобранное",
    111700784: "Sorgular",
    111700788: "yeni sifariş",
    111700792: "icra olunur",
    111702372: "gözləmə",
    111702376: "görüş",
    111702380: "quraşdırma",
    111702384: "müzakirə",
    142: "Uğurla tamamlandı",
    143: "İmtina olundu",
}
NIZAMI_STAGES = {
    "nerazobrannoye": 109988180,
    "sorgular": 109988196,
    "danisiqlar": 112086092,
    "telimat": 112086096,
    "yeni_sifaris": 112086100,
    "gozleme": 110774676,
    "muzakire": 110722180,
    "ugurlu": 142,
    "imtina": 143,
}
NIZAMI_STAGE_NAMES = {
    109988180: "Неразобранное",
    109988196: "yeni sorgu",
    112086092: "danışıqlar",
    112086096: "təlimat",
    112086100: "yeni sifariş",
    110774676: "gözləmə",
    110722180: "Müzakirə",
    142: "Uğurla tamamlandı",
    143: "İmtina olundu",
}
# Canonical task assignees.  Old account names deliberately do not appear here:
# they may remain in historical Kommo task text, but can no longer receive work.
KOMMO_TASK_RESPONSIBLE_USER_ID = 10932455
ACTIVE_ASSIGNEES = {
    # Employees work through personal funnels; Kommo stores the task under
    # the active shared administrator. The legacy 15532668 is read-only
    # historical metadata and is rejected by Kommo for new assignments.
    "rufat": ("Rüfət Həsənzadə", "Rüfət", KOMMO_TASK_RESPONSIBLE_USER_ID),
    "huseyn": ("Hüseyn Səfərov", "Hüseyn", KOMMO_TASK_RESPONSIBLE_USER_ID),
    "rasim": ("Rasim Əsgərov", "Rasim", KOMMO_TASK_RESPONSIBLE_USER_ID),
    "sermaye": ("Sərmayə Əhmədsoy", "Sərmayə", KOMMO_TASK_RESPONSIBLE_USER_ID),
    "asya": ("Asya Agayeva", "Asya", KOMMO_TASK_RESPONSIBLE_USER_ID),
    "nurane": ("Nuranə Şirinova", "Nuranə", KOMMO_TASK_RESPONSIBLE_USER_ID),
    "admin": ("Nizami Qasımov", "Özüm", KOMMO_TASK_RESPONSIBLE_USER_ID),
}
_UPD_MARKER = {short: (name, user_id) for name, short, user_id in ACTIVE_ASSIGNEES.values()}

STAGES = {
    "nerazobrannoye": 66107683,
    "danisiqlar": 108537924,
    "qiymet_teklifi": 66107691,
    "teqdimat": 66107699,
    "teqdimat_olundu": 96880440,
    "yeni_sifaris": 94525176,
    "gorus": 108537892,
    "daxili_muzakire": 108538104,
    "qurashdirma": 108537896,
    "dusunur": 108537976,
    "ugurlu": 142,
    "imtina": 143,
}
STAGE_NAMES = {
    66107683: "Nerazobrannoye",
    108537924: "danışıqlar",
    66107691: "Qiymət təklifi",
    66107699: "Təqdimat",
    96880440: "Təqdimat olundu",
    94525176: "yeni sifariş",
    108537892: "görüş",
    108538104: "daxili müzakirə",
    108537896: "quraşdırma",
    108537976: "Düşünür",
    142: "Uğurla tamamlandı",
    143: "İmtina olundu",
}
# Notify Admin only when a Sövdələşmələr deal moves to "Nömrə alınıb".
# Kommo status ID verified from the pipeline configuration.
NOTIFY_STAGE_ID = 108537924
KOMMO_USERS = {
    10932455: "Nizami Qasımov",
    15532668: "Admin",
}
_STAGE_TASK_TEXTS = {
    "qiymet_teklifi": "Qiymət təklifini göndər",
    "teqdimat": "Müştəriyə təqdimat keçirmək",
    "yeni_sifaris": "Yeni sifarişi rəsmiləşdirmək",
    "gorus": "Müştəri ilə görüş keçirmək",
    "qurashdirma": "Quraşdırmanı həyata keçirmək",
}
TASK_TYPE_NAMES = {
    1: "Əlaqə saxla",
    2: "Görüş",
    3263995: "Təqdimat",
    3263999: "Quraşdırma",
    3267595: "Zəng et",
    4229224: "Cavab gözlənilir",
    4232112: "aktiv",
    4232108: "Import",
    4239844: "passiv",
}
