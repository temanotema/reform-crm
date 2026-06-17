"""
yclients.py — интеграция с YCLIENTS REST API (только чтение).

По номеру телефона клиента получает из YCLIENTS его реальные данные:
количество визитов, последний визит, ближайшую запись, сумму покупок и
дату рождения.

ВАЖНО: модуль НИЧЕГО не создаёт, не редактирует и не удаляет в YCLIENTS —
только читает. Здесь нет ни одной функции записи.

Главная функция — get_profile_summary(phone) -> dict | None.
Если ключи не настроены или клиент не найден / ошибка — возвращает None,
и бот показывает данные из локальной базы (без падений).

Документация: https://developer.yclients.com
Партнёрский токен: https://yclients.com/appstore/developers/registration

Авторизация:
    Authorization: Bearer <partner_token>, User <user_token>
    Accept: application/vnd.api.v2+json

Пути API (проверено на боевом аккаунте):
    POST /auth                                  — получить user_token
    POST /company/{company_id}/clients/search   — найти клиента по телефону
    GET  /client/{company_id}/{client_id}       — карточка клиента (визиты, суммы)
    GET  /records/{company_id}?client_id=...     — записи клиента (даты визитов)
"""

import logging
import asyncio
from datetime import datetime, date, timedelta

import aiohttp

# Настройки берём из config.py через getattr — чтобы модуль не падал,
# если каких-то переменных там нет (тогда интеграция просто выключена).
import config

logger = logging.getLogger(__name__)

API_BASE = "https://api.yclients.com/api/v1"
ACCEPT_HEADER = "application/vnd.api.v2+json"

# ── Чтение настроек ───────────────────────────────────────────────────────────
PARTNER_TOKEN = getattr(config, "YCLIENTS_PARTNER_TOKEN", "") or ""
USER_TOKEN    = getattr(config, "YCLIENTS_USER_TOKEN", "") or ""
LOGIN         = getattr(config, "YCLIENTS_LOGIN", "") or ""
PASSWORD      = getattr(config, "YCLIENTS_PASSWORD", "") or ""
COMPANY_ID    = getattr(config, "YCLIENTS_COMPANY_ID", "") or ""
VIP_THRESHOLD = float(getattr(config, "YCLIENTS_VIP_THRESHOLD", 200000) or 200000)
# Поле «суммы покупок» для VIP: "paid" (оплачено) или "spent" (потрачено).
PAID_FIELD    = getattr(config, "YCLIENTS_PAID_FIELD", "paid") or "paid"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=12)

# Кэш user_token в памяти процесса.
_user_token_cache = USER_TOKEN or None

# Кэш сводок по телефону, чтобы не дёргать API на каждый клик.
_summary_cache = {}          # phone -> (expires_at, data|None)
_SUMMARY_TTL = timedelta(minutes=5)


def is_configured() -> bool:
    """True, если задан минимум: partner-токен, company_id и либо готовый
    user-токен, либо логин+пароль для его получения."""
    if not PARTNER_TOKEN or not COMPANY_ID:
        return False
    if _user_token_cache or USER_TOKEN:
        return True
    return bool(LOGIN and PASSWORD)


def _auth_header(with_user: bool = True) -> str:
    if with_user and _user_token_cache:
        return f"Bearer {PARTNER_TOKEN}, User {_user_token_cache}"
    return f"Bearer {PARTNER_TOKEN}"


def _headers(with_user: bool = True) -> dict:
    return {
        "Accept": ACCEPT_HEADER,
        "Content-Type": "application/json",
        "Authorization": _auth_header(with_user),
    }


# ── Низкоуровневые запросы (только GET/POST для чтения) ────────────────────────

async def _request(session, method, path, *, params=None, json=None, with_user=True):
    url = f"{API_BASE}{path}"
    try:
        async with session.request(
            method, url, params=params, json=json,
            headers=_headers(with_user), timeout=REQUEST_TIMEOUT,
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logger.warning("YClients %s %s -> HTTP %s: %s",
                               method, path, resp.status, text[:300])
                return None
            try:
                return await resp.json(content_type=None)
            except Exception:
                logger.warning("YClients %s %s: не JSON: %s", method, path, text[:200])
                return None
    except asyncio.TimeoutError:
        logger.warning("YClients %s %s: таймаут", method, path)
    except aiohttp.ClientError as e:
        logger.warning("YClients %s %s: сетевая ошибка: %s", method, path, e)
    return None


async def authenticate(session) -> bool:
    """Получает user_token по логину/паролю и кладёт его в кэш. True при успехе."""
    global _user_token_cache
    if _user_token_cache:
        return True
    if not (LOGIN and PASSWORD):
        return False
    data = await _request(
        session, "POST", "/auth",
        json={"login": LOGIN, "password": PASSWORD},
        with_user=False,
    )
    if data and data.get("success") and data.get("data", {}).get("user_token"):
        _user_token_cache = data["data"]["user_token"]
        logger.info("YClients: user_token получен")
        return True
    logger.warning("YClients: не удалось авторизоваться (проверьте логин/пароль)")
    return False


# ── Доменные запросы ──────────────────────────────────────────────────────────

def _digits(phone: str) -> str:
    return "".join(ch for ch in str(phone) if ch.isdigit())


async def search_client_id(session, phone: str):
    """Ищет клиента по телефону, возвращает его id (или None)."""
    phone_digits = _digits(phone)
    body = {
        "page": 1,
        "page_size": 10,
        "fields": ["id", "name", "phone"],
        "filters": [
            {"type": "quick_search", "state": {"value": phone_digits}},
        ],
    }
    data = await _request(
        session, "POST", f"/company/{COMPANY_ID}/clients/search", json=body,
    )
    if not data or not data.get("success"):
        return None
    rows = data.get("data") or []
    tail = phone_digits[-10:]
    for row in rows:
        if _digits(row.get("phone", ""))[-10:] == tail:
            return row.get("id")
    return rows[0].get("id") if rows else None


async def get_client_details(session, client_id):
    """Карточка клиента с агрегатами (визиты, суммы, дата рождения).

    Правильный путь — /client/{company_id}/{id} (единственное число,
    БЕЗ /company/ и без 's'). Поиск — отдельный метод (clients/search)."""
    data = await _request(
        session, "GET", f"/client/{COMPANY_ID}/{client_id}",
    )
    if not data or not data.get("success"):
        return None
    return data.get("data")


async def get_visit_dates(session, client_id):
    """
    Возвращает (first_visit_dt, last_visit_dt, nearest_dt) из записей клиента:
      first_visit_dt — самый ранний прошедший визит (или None),
      last_visit_dt  — последний прошедший визит (или None),
      nearest_dt     — ближайшая будущая запись (или None).

    Карточка клиента эти даты не отдаёт — берём из списка записей (records)
    за всю историю одним запросом.
    """
    today = date.today()
    params = {
        "client_id": client_id,
        "start_date": "2010-01-01",
        "end_date": (today + timedelta(days=365)).isoformat(),
        "count": 1000,
        "page": 1,
    }
    data = await _request(
        session, "GET", f"/records/{COMPANY_ID}", params=params,
    )
    if not data or not data.get("success"):
        return None, None, None
    now = datetime.now()
    past, future = [], []
    for rec in (data.get("data") or []):
        if rec.get("deleted"):
            continue
        dt = _parse_dt(rec.get("datetime") or rec.get("date"))
        if not dt:
            continue
        (future if dt >= now else past).append(dt)
    first_visit_dt = min(past) if past else None
    last_visit_dt = max(past) if past else None
    nearest_dt = min(future) if future else None
    return first_visit_dt, last_visit_dt, nearest_dt


async def get_day_records(session, target_date):
    """
    Все записи компании на конкретную дату — для напоминаний о визите.
    Возвращает список словарей: record_id, datetime, phone, client_name, master.
    """
    params = {
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
        "count": 1000,
        "page": 1,
    }
    data = await _request(session, "GET", f"/records/{COMPANY_ID}", params=params)
    if not data or not data.get("success"):
        return []
    out = []
    for rec in (data.get("data") or []):
        if rec.get("deleted"):
            continue
        dt = _parse_dt(rec.get("datetime") or rec.get("date"))
        if not dt or dt.date() != target_date:
            continue
        client = rec.get("client") or {}
        staff = rec.get("staff") or {}
        master = staff.get("name") or rec.get("staff_name") or ""
        out.append({
            "record_id": rec.get("id"),
            "datetime":  dt,
            "phone":     client.get("phone") or "",
            "client_name": client.get("name") or "",
            "master":    master,
        })
    return out


async def get_appointments_for_date(target_date):
    """
    Высокоуровневая обёртка для бота: записи компании на дату.
    Возвращает [] если интеграция не настроена или произошла ошибка.
    """
    if not is_configured():
        return []
    async with aiohttp.ClientSession() as session:
        try:
            if not await authenticate(session):
                return []
            return await get_day_records(session, target_date)
        except Exception as e:
            logger.warning("YClients: ошибка чтения записей на %s: %s", target_date, e)
            return []


# ── Парсинг ───────────────────────────────────────────────────────────────────

def _parse_dt(value):
    if not value:
        return None
    s = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.split("+")[0].strip())
    except ValueError:
        return None


def _parse_date(value):
    dt = _parse_dt(value)
    return dt.date() if dt else None


def _to_float(value):
    if value is None or value == "":
        return 0.0
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except ValueError:
        return 0.0


# ── Высокоуровневая сводка ────────────────────────────────────────────────────

async def _build_summary(session, phone: str):
    if not await authenticate(session):
        return None

    client_id = await search_client_id(session, phone)
    if not client_id:
        return None  # в YCLIENTS такого телефона нет

    details = await get_client_details(session, client_id) or {}
    first_visit_dt, last_visit_dt, nearest_dt = await get_visit_dates(session, client_id)

    def _first(*keys, default=None):
        for k in keys:
            v = details.get(k)
            if v not in (None, ""):
                return v
        return default

    total_paid = _to_float(_first(PAID_FIELD, "paid", "spent", "sold_amount", default=0))

    # Последний визит: из карточки (если вдруг есть), иначе из записей.
    last_visit = _parse_date(_first("last_visit_date", "last_visit"))
    if not last_visit and last_visit_dt:
        last_visit = last_visit_dt.date()

    # Первый визит: карточка его не отдаёт, поэтому берём самый ранний визит
    # из истории записей (records). Нужен для строки «Вы с нами уже N дней».
    first_visit = _parse_date(_first("first_visit_date", "first_visit"))
    if not first_visit and first_visit_dt:
        first_visit = first_visit_dt.date()

    summary = {
        "client_id": client_id,
        "name":        _first("name", "display_name", "fullname", default="") or "",
        "phone":       _first("phone", default=_digits(phone)),
        "visits":      int(_to_float(_first("visits", "visit_count", "visits_count", default=0))),
        "last_visit":  last_visit,
        "first_visit": first_visit,
        "birth_date":  _parse_date(_first("birth_date", "birthday")),
        "total_paid":  total_paid,
        "is_vip":      total_paid >= VIP_THRESHOLD,
        "nearest_dt":  nearest_dt,
        "nearest":     nearest_dt.date() if nearest_dt else None,
    }
    return summary


async def get_profile_summary(phone: str):
    """
    Главная функция для бота. Сводка по клиенту из YCLIENTS или None.
    Кэшируется на несколько минут.
    """
    if not is_configured() or not phone:
        return None

    key = _digits(phone)
    cached = _summary_cache.get(key)
    if cached and cached[0] > datetime.now():
        return cached[1]

    async with aiohttp.ClientSession() as session:
        try:
            summary = await _build_summary(session, phone)
        except Exception as e:
            logger.warning("YClients: ошибка сводки для %s: %s", key, e)
            summary = None

    _summary_cache[key] = (datetime.now() + _SUMMARY_TTL, summary)
    return summary


# ── Диагностика (запуск из консоли) ───────────────────────────────────────────
#     python yclients.py +79991234567
if __name__ == "__main__":
    import sys
    import json as _json

    async def _diag(phone):
        print("is_configured:", is_configured())
        print("company_id:", COMPANY_ID, "| partner token:",
              ("задан" if PARTNER_TOKEN else "НЕТ"))
        if not is_configured():
            print("⚠️  Заполни ключи YClients в config.py — см. YCLIENTS_SETUP.md")
            return
        async with aiohttp.ClientSession() as session:
            ok = await authenticate(session)
            print("auth user_token:", "ok" if ok else "FAIL")
            cid = await search_client_id(session, phone)
            print("client_id:", cid)
            if cid:
                details = await get_client_details(session, cid)
                print("--- client details (raw) ---")
                print(_json.dumps(details, ensure_ascii=False, indent=2)[:2000])
                fv, lv, nr = await get_visit_dates(session, cid)
                print("first visit:", fv, "| last visit:", lv, "| nearest record:", nr)
        print("--- summary ---")
        print(await get_profile_summary(phone))

    if len(sys.argv) < 2:
        print("Использование: python yclients.py <телефон>")
    else:
        asyncio.run(_diag(sys.argv[1]))
