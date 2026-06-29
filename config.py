"""
config.py — настройки приложения (БЕЗ секретов, можно коммитить в git).

Реальные секреты (токен бота, пароли, доступы YClients) лежат в config_local.py
— этот файл в .gitignore и в репозиторий не попадает. Значения из config_local.py
переопределяют заглушки ниже.

Также любую настройку можно задать через переменную окружения.
"""

import os

# ── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")     # реальный токен — в config_local.py

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip()
]

# ── Клиника ───────────────────────────────────────────────────────────────────
CLINIC_NAME = os.getenv("CLINIC_NAME", "Re. form Cosmetology")

AFTER_PHONE_MESSAGE = os.getenv(
    "AFTER_PHONE_MESSAGE",
    "Добрый день! 🌸\n\n"
    "Меня зовут Анна, я администратор клиники Re.form Cosmetology.\n\n"
    "Спасибо, что подтвердили свой номер. Мы всегда на связи — "
    "задавайте любые вопросы, и я отвечу в ближайшее время!",
)

# ── Веб-панель ────────────────────────────────────────────────────────────────
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")   # ⚠️ поставь свой в config_local.py
WEB_PORT       = int(os.getenv("WEB_PORT", "5000"))
SECRET_KEY     = os.getenv("SECRET_KEY", "change_me_in_production_32chars!")

# ── PostgreSQL ────────────────────────────────────────────────────────────────
# Пароль БД — в config_local.py (не в git). DATABASE_URL собирается ниже,
# уже ПОСЛЕ применения config_local.
DB_HOST     = os.getenv("DB_HOST",     "localhost")
DB_PORT     = os.getenv("DB_PORT",     "5432")
DB_NAME     = os.getenv("DB_NAME",     "cosmo_db")
DB_USER     = os.getenv("DB_USER",     "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# ── YCLIENTS API ──────────────────────────────────────────────────────────────
# Секреты (partner-токен, логин, пароль) — в config_local.py. Здесь пусто.
YCLIENTS_PARTNER_TOKEN = os.getenv("YCLIENTS_PARTNER_TOKEN", "")
YCLIENTS_USER_TOKEN    = os.getenv("YCLIENTS_USER_TOKEN", "")
YCLIENTS_LOGIN         = os.getenv("YCLIENTS_LOGIN", "")
YCLIENTS_PASSWORD      = os.getenv("YCLIENTS_PASSWORD", "")
YCLIENTS_COMPANY_ID    = os.getenv("YCLIENTS_COMPANY_ID", "923489")

# Порог суммы покупок для автоматического статуса VIP (в рублях).
YCLIENTS_VIP_THRESHOLD = float(os.getenv("YCLIENTS_VIP_THRESHOLD", "200000"))

# Какое поле YCLIENTS считать «суммой покупок» для VIP: "paid" или "spent".
YCLIENTS_PAID_FIELD = os.getenv("YCLIENTS_PAID_FIELD", "paid")

# Ссылка на клиента в кабинете YCLIENTS (база клиентов, фильтр по телефону).
YCLIENTS_CLIENT_URL_TEMPLATE = os.getenv(
    "YCLIENTS_CLIENT_URL_TEMPLATE",
    "https://yclients.com/clients/{company_id}/base/"
    "?fields%5B0%5D=name&fields%5B1%5D=phone&fields%5B2%5D=email"
    "&fields%5B3%5D=sold_amount&fields%5B4%5D=visits_count"
    "&fields%5B5%5D=last_visit_date&fields%5B6%5D=first_visit_date"
    "&order_by=id&order_by_direction=desc&page=1&page_size=25&operation=AND"
    "&filters%5B1%5D%5Btype%5D=quick_search"
    "&filters%5B1%5D%5Bstate%5D%5Bvalue%5D={query}",
)

# ── Автообновление (GitHub Releases) ───────────────────────────────────────────
GITHUB_REPO = os.getenv("GITHUB_REPO", "temanotema/reform-crm")

# ── Мониторинг (Sentry) ─────────────────────────────────────────────────────────
# Пока DSN пустой — мониторинг выключен (ничего никуда не отправляется).
# Реальный ключ положи в config_local.py: SENTRY_DSN = "https://...".
# Персональные данные в события не попадают (вырезаются в monitoring.py).
SENTRY_DSN         = os.getenv("SENTRY_DSN", "")
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT", "local")   # на сервере поставь "production"


# ── Локальные секреты (НЕ в git) ──────────────────────────────────────────────
# config_local.py переопределяет значения выше реальными данными.
try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass

# DATABASE_URL собираем ПОСЛЕ config_local — чтобы пароль из него попал в строку.
DATABASE_URL = os.getenv("DATABASE_URL") or \
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
