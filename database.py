"""
database.py — слой данных (PostgreSQL через psycopg2).
"""

import re
import logging
import threading
import psycopg2
import psycopg2.extras
import psycopg2.pool
from contextlib import contextmanager
from config import DATABASE_URL

logger = logging.getLogger(__name__)


# ── Пул соединений ────────────────────────────────────────────────────────────
# Раньше на каждый запрос открывалось НОВОЕ подключение к PostgreSQL — это
# медленно (особенно при частом опросе). Пул переиспользует соединения.

_POOL = None
_POOL_LOCK = threading.Lock()


def _get_pool():
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = psycopg2.pool.ThreadedConnectionPool(1, 20, DATABASE_URL)
    return _POOL


# Ошибки уровня соединения (обрыв сети, рестарт БД, протухшее соединение).
_CONN_ERRORS = (psycopg2.OperationalError, psycopg2.InterfaceError)


def _reset_pool():
    """Закрывает текущий пул и сбрасывает его — следующий запрос поднимет новый
    с живыми соединениями. Нужно после обрыва/рестарта БД."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            try:
                _POOL.closeall()
            except Exception:
                pass
            _POOL = None


def _with_retry(run):
    """Выполняет запрос; при потере соединения переподключается и повторяет один раз."""
    try:
        return run()
    except _CONN_ERRORS as e:
        logger.warning("Соединение с БД потеряно (%s) — переподключаюсь и повторяю", e)
        _reset_pool()
        return run()


# ── Утилита нормализации телефона ─────────────────────────────────────────────

def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return digits


# ── Соединение ────────────────────────────────────────────────────────────────

@contextmanager
def get_conn():
    pool = _get_pool()
    conn = pool.getconn()
    broken = False
    try:
        conn.autocommit = False
        yield conn
        conn.commit()
    except _CONN_ERRORS:
        broken = True
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        # битое соединение НЕ возвращаем в пул (close=True), чтобы не переиспользовать.
        try:
            pool.putconn(conn, close=broken or bool(getattr(conn, "closed", 0)))
        except Exception:
            pass


def fetchall(sql, params=()):
    def run():
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params)
            return cur.fetchall()
    return _with_retry(run)


def fetchone(sql, params=()):
    def run():
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params)
            return cur.fetchone()
    return _with_retry(run)


def execute(sql, params=()):
    def run():
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.rowcount
    return _with_retry(run)


def execute_returning(sql, params=()):
    def run():
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params)
            return cur.fetchone()
    return _with_retry(run)


# ── Инициализация БД ──────────────────────────────────────────────────────────

def init_db():
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id                  SERIAL PRIMARY KEY,
                tg_id               BIGINT UNIQUE NOT NULL,
                username            TEXT DEFAULT '',
                first_name          TEXT DEFAULT '',
                last_name           TEXT DEFAULT '',
                patronymic          TEXT DEFAULT '',
                phone               TEXT DEFAULT '',
                notes               TEXT DEFAULT '',
                created_at          TIMESTAMPTZ DEFAULT NOW(),
                phone_confirmed_at  TIMESTAMPTZ,
                unread_count        INT DEFAULT 0
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id          SERIAL PRIMARY KEY,
                client_id   INT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                direction   TEXT NOT NULL,
                text        TEXT NOT NULL,
                created_at  TIMESTAMPTZ DEFAULT NOW(),
                is_read     BOOLEAN DEFAULT FALSE
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id              SERIAL PRIMARY KEY,
                client_id       INT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                client_phone        TEXT NOT NULL,
                client_name         TEXT NOT NULL,
                client_first_name   TEXT DEFAULT '',
                client_last_name    TEXT DEFAULT '',
                client_patronymic   TEXT DEFAULT '',
                master_name         TEXT NOT NULL,
                booking_time    TIME NOT NULL,
                booking_date    DATE NOT NULL,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                reminder_sent   BOOLEAN DEFAULT FALSE,
                status          TEXT DEFAULT 'active'
            )
        """)

        # Врачи
        cur.execute("""
            CREATE TABLE IF NOT EXISTS doctors (
                id         SERIAL PRIMARY KEY,
                full_name  TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Категории клиентов
        cur.execute("""
            CREATE TABLE IF NOT EXISTS client_categories (
                id         SERIAL PRIMARY KEY,
                name       TEXT NOT NULL UNIQUE,
                color      TEXT DEFAULT '#c06090',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Связь клиент ↔ категория (многие ко многим)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS client_category_map (
                client_id   INT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                category_id INT NOT NULL REFERENCES client_categories(id) ON DELETE CASCADE,
                PRIMARY KEY (client_id, category_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_templates (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Дедупликация отправленных напоминаний (по id записи YCLIENTS).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS yc_reminders_sent (
                record_id BIGINT PRIMARY KEY,
                sent_at   TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Дедупликация уведомлений «вы записаны» (по id записи YCLIENTS).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS yc_bookings_notified (
                record_id BIGINT PRIMARY KEY,
                sent_at   TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # «Замеченные» записи: запись должна встретиться минимум в двух опросах
        # подряд, прежде чем по ней уйдёт подтверждение. Защита от записей,
        # которые создали и тут же удалили (тест/ошибка).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS yc_bookings_seen (
                record_id  BIGINT PRIMARY KEY,
                first_seen TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Дедупликация поздравлений с ДР (по клиенту и году).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS birthday_sent (
                client_id INT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                year      INT NOT NULL,
                sent_at   TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (client_id, year)
            )
        """)

        # Аналитика: события бота (что нажимают/выбирают). Без перс. данных —
        # только тип события, необязательная деталь (напр. категория) и время.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_events (
                id         SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                detail     TEXT DEFAULT '',
                client_id  INT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Кэш сводок YClients (по телефону) — чтобы профиль открывался мгновенно
        # и переживал недоступность YClients (отдаём последние известные данные).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS yc_cache (
                phone      TEXT PRIMARY KEY,
                data       TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                endpoint   TEXT PRIMARY KEY,
                p256dh     TEXT NOT NULL,
                auth       TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_client  ON messages(client_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_events_type    ON bot_events(event_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_events_created ON bot_events(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_date    ON bookings(booking_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_client  ON bookings(client_id)")

    _safe_alter("ALTER TABLE clients  ADD COLUMN IF NOT EXISTS unread_count INT DEFAULT 0")
    _safe_alter("ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT FALSE")
    _safe_alter("ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_type TEXT")
    _safe_alter("ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_file_id TEXT")
    _safe_alter("ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_filename TEXT")
    _safe_alter("ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_local_path TEXT")
    _safe_alter("ALTER TABLE messages ADD COLUMN IF NOT EXISTS sent_by TEXT")  # имя админа-отправителя
    _safe_alter("""CREATE TABLE IF NOT EXISTS admin_users (
        id            SERIAL PRIMARY KEY,
        login         TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        name          TEXT DEFAULT '',
        is_super      BOOLEAN DEFAULT FALSE,
        is_active     BOOLEAN DEFAULT TRUE,
        token         TEXT DEFAULT '',
        theme         TEXT DEFAULT 'light',
        wallpaper     TEXT DEFAULT 'default',
        created_at    TIMESTAMPTZ DEFAULT NOW()
    )""")
    _safe_alter("ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS theme TEXT DEFAULT 'light'")
    _safe_alter("ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS wallpaper TEXT DEFAULT 'default'")
    # Супер-админ = текущий логин/пароль из config (создаётся один раз, пароль не перезаписывается).
    try:
        import config as _cfg
        seed_super_admin(getattr(_cfg, "ADMIN_LOGIN", "admin"),
                         getattr(_cfg, "ADMIN_PASSWORD", ""), "Супер-админ")
    except Exception as e:
        logger.warning("seed super admin: %s", e)
    _safe_alter("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_sent BOOLEAN DEFAULT FALSE")
    _safe_alter("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'")
    _safe_alter("ALTER TABLE clients ADD COLUMN IF NOT EXISTS patronymic TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS client_first_name TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS client_last_name TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS client_patronymic TEXT DEFAULT ''")
    # Поля анкеты из бота (ФИО + дата рождения). Их не затирает Telegram-имя.
    _safe_alter("ALTER TABLE clients ADD COLUMN IF NOT EXISTS reg_last_name  TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE clients ADD COLUMN IF NOT EXISTS reg_first_name TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE clients ADD COLUMN IF NOT EXISTS reg_patronymic TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE clients ADD COLUMN IF NOT EXISTS birth_date DATE")
    _safe_alter("ALTER TABLE client_categories ADD COLUMN IF NOT EXISTS protected BOOLEAN DEFAULT FALSE")
    # Врачи (для кнопки «Наши врачи» в боте): должность, фото, порядок.
    _safe_alter("ALTER TABLE doctors ADD COLUMN IF NOT EXISTS title TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE doctors ADD COLUMN IF NOT EXISTS photo TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE doctors ADD COLUMN IF NOT EXISTS sort_order INT DEFAULT 0")

    _init_message_templates()
    init_bot_settings()
    ensure_vip_category()   # защищённая категория «VIP» (нельзя удалить)

    logger.info("DB initialized")


def _init_message_templates():
    from templates import DEFAULT_TEMPLATES
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS message_templates (
                key   TEXT PRIMARY KEY,
                text  TEXT NOT NULL
            )
        """)
        for key, meta in DEFAULT_TEMPLATES.items():
            cur.execute(
                "INSERT INTO message_templates (key, text) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
                (key, meta["text"]),
            )


def _safe_alter(sql):
    try:
        with get_conn() as conn:
            conn.cursor().execute(sql)
    except Exception as e:
        logger.debug("alter note: %s", e)


# ── CLIENTS ───────────────────────────────────────────────────────────────────

def upsert_client(tg_id, username="", first_name="", last_name="", patronymic=""):
    row = fetchone("SELECT id FROM clients WHERE tg_id=%s", (tg_id,))
    if row:
        execute(
            "UPDATE clients SET username=%s, first_name=%s, last_name=%s, patronymic=%s WHERE tg_id=%s",
            (username, first_name, last_name, patronymic, tg_id),
        )
        return row["id"]
    row = execute_returning(
        "INSERT INTO clients (tg_id,username,first_name,last_name,patronymic) VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (tg_id, username, first_name, last_name, patronymic),
    )
    return row["id"]


def update_client_name(client_id, first_name="", last_name="", patronymic=""):
    execute(
        "UPDATE clients SET first_name=%s, last_name=%s, patronymic=%s WHERE id=%s",
        (first_name, last_name, patronymic, client_id),
    )


def client_display_name(client):
    if not client:
        return ""
    # Приоритет: ФИО из анкеты бота (reg_*, их не затирает Telegram-имя),
    # затем редактируемые поля, затем имя из Telegram.
    reg_parts = [
        client.get("reg_last_name") or "",
        client.get("reg_first_name") or "",
        client.get("reg_patronymic") or "",
    ]
    reg_name = " ".join(p.strip() for p in reg_parts if p and p.strip())
    if reg_name:
        return reg_name
    parts = [
        client.get("last_name") or "",
        client.get("first_name") or "",
        client.get("patronymic") or "",
    ]
    name = " ".join(p.strip() for p in parts if p and p.strip())
    if name:
        return name
    return f"{client.get('first_name') or ''} {client.get('last_name') or ''}".strip()


def save_client_phone(tg_id, phone):
    phone = normalize_phone(phone)
    execute(
        "UPDATE clients SET phone=%s, phone_confirmed_at=NOW() WHERE tg_id=%s",
        (phone, tg_id),
    )


def get_client_by_tg(tg_id):
    return fetchone("SELECT * FROM clients WHERE tg_id=%s", (tg_id,))


def get_client(client_id):
    return fetchone("SELECT * FROM clients WHERE id=%s", (client_id,))


def get_client_by_phone(phone):
    phone = normalize_phone(phone)
    return fetchone("SELECT * FROM clients WHERE phone=%s", (phone,))


def get_all_clients():
    return fetchall("""
        SELECT c.*,
               (SELECT text       FROM messages WHERE client_id=c.id ORDER BY created_at DESC LIMIT 1) AS last_message,
               (SELECT created_at FROM messages WHERE client_id=c.id ORDER BY created_at DESC LIMIT 1) AS last_message_at,
               COALESCE(
                 (SELECT json_agg(json_build_object('id', cc.id, 'name', cc.name, 'color', cc.color))
                  FROM client_categories cc
                  JOIN client_category_map ccm ON ccm.category_id = cc.id
                  WHERE ccm.client_id = c.id), '[]'::json
               ) AS categories
        FROM clients c
        ORDER BY last_message_at DESC NULLS LAST, c.created_at DESC
    """)


def get_dialogs_light():
    """Лёгкая версия для частого опроса (без категорий) — только то, что нужно
    списку диалогов: id, непрочитанные, последнее сообщение и его время."""
    return fetchall("""
        SELECT c.id, c.unread_count, c.phone, c.username,
               c.first_name, c.last_name, c.patronymic,
               c.reg_first_name, c.reg_last_name, c.reg_patronymic,
               (SELECT text       FROM messages WHERE client_id=c.id ORDER BY created_at DESC LIMIT 1) AS last_message,
               (SELECT created_at FROM messages WHERE client_id=c.id ORDER BY created_at DESC LIMIT 1) AS last_message_at
        FROM clients c
        ORDER BY last_message_at DESC NULLS LAST, c.created_at DESC
    """)


def update_client_notes(client_id, notes):
    execute("UPDATE clients SET notes=%s WHERE id=%s", (notes, client_id))


def get_all_client_ids():
    """Все tg_id клиентов с реальным аккаунтом (для рассылки)."""
    return fetchall("SELECT tg_id, first_name FROM clients WHERE tg_id > 0")


def get_clients_by_category(category_id):
    """tg_id клиентов определённой категории."""
    return fetchall("""
        SELECT c.tg_id, c.first_name FROM clients c
        JOIN client_category_map ccm ON ccm.client_id = c.id
        WHERE ccm.category_id = %s AND c.tg_id > 0
    """, (category_id,))


# ── КАТЕГОРИИ ─────────────────────────────────────────────────────────────────

def get_all_categories():
    return fetchall("SELECT * FROM client_categories ORDER BY name")


def create_category(name, color="#c06090"):
    try:
        row = execute_returning(
            "INSERT INTO client_categories (name, color) VALUES (%s, %s) RETURNING id",
            (name, color),
        )
        return row["id"] if row else None
    except Exception:
        return None


def delete_category(cat_id):
    """Удаляет категорию. Защищённые (protected, напр. VIP) удалить нельзя."""
    row = fetchone("SELECT protected FROM client_categories WHERE id=%s", (cat_id,))
    if row and row.get("protected"):
        return False
    execute("DELETE FROM client_categories WHERE id=%s", (cat_id,))
    return True


def ensure_vip_category():
    """Возвращает id защищённой категории «VIP» (создаёт её при необходимости)."""
    row = fetchone("SELECT id FROM client_categories WHERE name=%s", ("VIP",))
    if row:
        execute("UPDATE client_categories SET protected=TRUE WHERE id=%s", (row["id"],))
        return row["id"]
    row = execute_returning(
        "INSERT INTO client_categories (name, color, protected) VALUES (%s, %s, TRUE) RETURNING id",
        ("VIP", "#b08d57"),
    )
    return row["id"] if row else None


def add_client_to_category(client_id, category_id):
    """Добавляет клиента в категорию (без удаления остальных). Идемпотентно."""
    if not client_id or not category_id:
        return
    try:
        execute(
            "INSERT INTO client_category_map (client_id, category_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (client_id, category_id),
        )
    except Exception:
        pass


def tag_client_vip(client_id):
    """Помечает клиента VIP (только добавляет; вручную поставленный VIP не трогаем)."""
    add_client_to_category(client_id, ensure_vip_category())


def set_client_categories(client_id, category_ids):
    """Полностью перезаписывает категории клиента."""
    execute("DELETE FROM client_category_map WHERE client_id=%s", (client_id,))
    for cid in category_ids:
        try:
            execute(
                "INSERT INTO client_category_map (client_id, category_id) VALUES (%s, %s)",
                (client_id, cid),
            )
        except Exception:
            pass


def get_client_categories(client_id):
    return fetchall("""
        SELECT cc.* FROM client_categories cc
        JOIN client_category_map ccm ON ccm.category_id = cc.id
        WHERE ccm.client_id = %s
    """, (client_id,))


# ── ВРАЧИ (для кнопки «Наши врачи» в боте) ────────────────────────────────────

def get_all_doctors():
    return fetchall("SELECT * FROM doctors ORDER BY sort_order, id")


def clear_doctors():
    execute("DELETE FROM doctors")


def add_doctor(full_name, title="", photo="", sort_order=0):
    row = execute_returning(
        "INSERT INTO doctors (full_name, title, photo, sort_order) VALUES (%s, %s, %s, %s) RETURNING id",
        (full_name, title, photo, sort_order),
    )
    return row["id"] if row else None


# ── WEB PUSH: подписки браузеров админа ───────────────────────────────────────

def add_push_subscription(endpoint, p256dh, auth):
    execute(
        "INSERT INTO push_subscriptions (endpoint, p256dh, auth) VALUES (%s, %s, %s) "
        "ON CONFLICT (endpoint) DO UPDATE SET p256dh=EXCLUDED.p256dh, auth=EXCLUDED.auth",
        (endpoint, p256dh, auth),
    )


def get_push_subscriptions():
    return fetchall("SELECT endpoint, p256dh, auth FROM push_subscriptions")


def delete_push_subscription(endpoint):
    execute("DELETE FROM push_subscriptions WHERE endpoint=%s", (endpoint,))


# ── MESSAGES ──────────────────────────────────────────────────────────────────

def save_message(client_id, direction, text, media_type=None, media_file_id=None,
                 media_filename=None, media_local_path=None, sent_by=None):
    execute_returning(
        """INSERT INTO messages
           (client_id, direction, text, media_type, media_file_id, media_filename, media_local_path, sent_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (client_id, direction, text, media_type, media_file_id, media_filename, media_local_path, sent_by),
    )
    if direction == "in":
        execute("UPDATE clients SET unread_count = unread_count + 1 WHERE id=%s", (client_id,))


# ── ADMIN USERS (мультиадмин + супер-админ) ───────────────────────────────────

import secrets as _secrets
from werkzeug.security import generate_password_hash as _hash_pw, check_password_hash as _check_pw


def _new_admin_token():
    return _secrets.token_hex(16)


def seed_super_admin(login, password, name="Супер-админ"):
    """Супер-админ = логин/пароль из config (источник истины). Создаёт при первом запуске,
    при последующих — обновляет пароль/имя из config (поменял пароль в config_local +
    рестарт → он применится). Токен НЕ трогаем, чтобы рестарт сам по себе не разлогинивал;
    для выхода есть кнопка «Разлогинить всех» в /adm."""
    if not login or not password:
        return
    existing = get_admin_by_login(login)
    if existing:
        execute(
            "UPDATE admin_users SET password_hash=%s, name=%s, is_super=TRUE, is_active=TRUE WHERE id=%s",
            (_hash_pw(password), name, existing["id"]),
        )
    else:
        execute(
            """INSERT INTO admin_users (login, password_hash, name, is_super, is_active, token)
               VALUES (%s,%s,%s,TRUE,TRUE,%s)""",
            (login, _hash_pw(password), name, _new_admin_token()),
        )


def get_admin_by_login(login):
    return fetchone("SELECT * FROM admin_users WHERE login=%s", (login,))


def get_admin(admin_id):
    return fetchone("SELECT * FROM admin_users WHERE id=%s", (admin_id,))


def get_all_admins():
    return fetchall("SELECT * FROM admin_users ORDER BY is_super DESC, name, login")


def verify_admin(login, password):
    """Возвращает строку админа, если логин+пароль верны и аккаунт активен, иначе None."""
    a = get_admin_by_login((login or "").strip())
    if a and a.get("is_active") and _check_pw(a["password_hash"], password or ""):
        return a
    return None


def admin_token_valid(admin_id, token):
    a = get_admin(admin_id)
    return bool(a and a.get("is_active") and token and a.get("token") == token)


def create_admin(login, password, name, is_super=False):
    """Создаёт аккаунт. Возвращает id или None, если логин занят/данные пустые."""
    login = (login or "").strip()
    if not login or not password:
        return None
    if get_admin_by_login(login):
        return None
    row = execute_returning(
        """INSERT INTO admin_users (login, password_hash, name, is_super, is_active, token)
           VALUES (%s,%s,%s,%s,TRUE,%s) RETURNING id""",
        (login, _hash_pw(password), (name or "").strip(), bool(is_super), _new_admin_token()),
    )
    return row["id"] if row else None


def delete_admin(admin_id):
    execute("DELETE FROM admin_users WHERE id=%s AND is_super=FALSE", (admin_id,))


def set_admin_password(admin_id, password):
    """Меняет пароль и ротирует токен — все текущие сессии админа аннулируются."""
    if not password:
        return
    execute("UPDATE admin_users SET password_hash=%s, token=%s WHERE id=%s",
            (_hash_pw(password), _new_admin_token(), admin_id))


def set_admin_name(admin_id, name):
    execute("UPDATE admin_users SET name=%s WHERE id=%s", ((name or "").strip(), admin_id))


def set_admin_active(admin_id, active):
    """Вкл/выкл аккаунт. Выключение + ротация токена = моментальный разлогин и блок входа."""
    execute("UPDATE admin_users SET is_active=%s, token=%s WHERE id=%s",
            (bool(active), _new_admin_token(), admin_id))


def force_logout_admin(admin_id):
    """Ротирует токен — текущие сессии админа становятся недействительными (вход остаётся)."""
    execute("UPDATE admin_users SET token=%s WHERE id=%s", (_new_admin_token(), admin_id))


def logout_all_admins():
    """Ротирует токены ВСЕХ админов (включая супер) — все обязаны войти заново.
    У каждого — свой уникальный токен."""
    execute("UPDATE admin_users SET token = md5(random()::text || clock_timestamp()::text || id::text)")


def get_admin_prefs(admin_id):
    """Личные настройки админа (тема, обои) — хранятся в аккаунте, а не в браузере."""
    a = get_admin(admin_id)
    if not a:
        return {"theme": "light", "wallpaper": "default"}
    return {"theme": a.get("theme") or "light", "wallpaper": a.get("wallpaper") or "default"}


def set_admin_pref(admin_id, key, value):
    """Сохраняет одну личную настройку. key из белого списка (защита имени колонки)."""
    if key not in ("theme", "wallpaper"):
        return
    execute(f"UPDATE admin_users SET {key}=%s WHERE id=%s", ((value or "")[:32], admin_id))


def admin_message_stats(period=None):
    """Сколько исходящих сообщений отправил каждый админ. period: 'week'|'month'|None(всё время)."""
    where = "direction='out' AND sent_by IS NOT NULL AND sent_by <> ''"
    params = []
    if period == "week":
        where += " AND created_at >= NOW() - INTERVAL '7 days'"
    elif period == "month":
        where += " AND created_at >= NOW() - INTERVAL '30 days'"
    return fetchall(
        f"SELECT sent_by AS name, COUNT(*) AS n FROM messages WHERE {where} "
        "GROUP BY sent_by ORDER BY n DESC", tuple(params),
    )


def get_message(message_id):
    return fetchone("SELECT * FROM messages WHERE id=%s", (message_id,))


def set_message_local_path(message_id, filename):
    """Запоминает локальный файл медиа — чтобы потом отдавать с диска, а не качать из Telegram."""
    execute("UPDATE messages SET media_local_path=%s WHERE id=%s", (filename, message_id))


def get_messages(client_id):
    return fetchall(
        "SELECT * FROM messages WHERE client_id=%s ORDER BY created_at ASC",
        (client_id,),
    )


def get_messages_since(client_id, after_id=0):
    return fetchall(
        "SELECT * FROM messages WHERE client_id=%s AND id > %s ORDER BY created_at ASC",
        (client_id, after_id),
    )


def mark_messages_read(client_id):
    execute("UPDATE messages SET is_read=TRUE WHERE client_id=%s AND direction='in'", (client_id,))
    execute("UPDATE clients SET unread_count=0 WHERE id=%s", (client_id,))


def get_total_unread():
    row = fetchone("SELECT COALESCE(SUM(unread_count),0) AS n FROM clients")
    return int(row["n"])


def get_unread_summary():
    """Сводка непрочитанного: число диалогов с непрочитанными и общее число."""
    row = fetchone(
        "SELECT COUNT(*) FILTER (WHERE unread_count > 0) AS dialogs, "
        "COALESCE(SUM(unread_count),0) AS total FROM clients"
    )
    return {"dialogs": int(row["dialogs"] or 0), "total": int(row["total"] or 0)}


def get_dashboard_stats():
    """Сводные показатели для мини-дашборда."""
    def n(sql, params=()):
        r = fetchone(sql, params)
        return int((r or {}).get("n") or 0)

    stats = {
        "total_clients": n("SELECT COUNT(*) AS n FROM clients"),
        "new_today": n("SELECT COUNT(*) AS n FROM clients WHERE created_at::date = CURRENT_DATE"),
        "new_7d": n("SELECT COUNT(*) AS n FROM clients WHERE created_at >= NOW() - INTERVAL '7 days'"),
        "msg_in_7d": n("SELECT COUNT(*) AS n FROM messages WHERE direction='in'  AND created_at >= NOW() - INTERVAL '7 days'"),
        "msg_out_7d": n("SELECT COUNT(*) AS n FROM messages WHERE direction='out' AND created_at >= NOW() - INTERVAL '7 days'"),
    }
    rows = fetchall("""
        SELECT to_char(d.day, 'DD.MM') AS label,
               COALESCE(COUNT(m.id), 0) AS cnt
        FROM generate_series(CURRENT_DATE - 6, CURRENT_DATE, INTERVAL '1 day') AS d(day)
        LEFT JOIN messages m ON m.created_at::date = d.day::date AND m.direction = 'in'
        GROUP BY d.day
        ORDER BY d.day
    """)
    stats["series"] = [{"label": r["label"], "cnt": int(r["cnt"])} for r in rows]
    s = get_unread_summary()
    stats["unread_dialogs"] = s["dialogs"]
    stats["unread_total"] = s["total"]
    return stats


# ── Аналитика событий бота ──────────────────────────────────────────────────────

def log_event(event_type, detail="", client_id=None):
    """Записывает событие бота (что нажали/выбрали). Без перс. данных.
    Лучшее-усилие: при ошибке только логируем, бота не роняем."""
    try:
        execute(
            "INSERT INTO bot_events (event_type, detail, client_id) VALUES (%s, %s, %s)",
            (event_type, detail or "", client_id),
        )
    except Exception as e:
        logger.debug("log_event(%s) failed: %s", event_type, e)


def get_bot_analytics(days=30):
    """Сводка по событиям бота за период: топ разделов, категории, воронка."""
    d = str(int(days))
    out = {"days": int(days)}

    section_labels = {"profile": "Профиль", "doctors": "Врачи", "contacts": "Контакты"}
    rows = fetchall(
        "SELECT event_type, COUNT(*) AS n FROM bot_events "
        "WHERE event_type = ANY(%s) AND created_at >= NOW() - (%s || ' days')::interval "
        "GROUP BY event_type ORDER BY n DESC",
        (list(section_labels.keys()), d),
    )
    out["sections"] = [{"label": section_labels.get(r["event_type"], r["event_type"]),
                        "n": int(r["n"])} for r in rows]

    def _distinct(event_type):
        r = fetchone(
            "SELECT COUNT(DISTINCT COALESCE(client_id, -id)) AS n FROM bot_events "
            "WHERE event_type=%s AND created_at >= NOW() - (%s || ' days')::interval",
            (event_type, d),
        )
        return int((r or {}).get("n") or 0)

    starts = _distinct("start")
    phones = _distinct("phone_confirmed")
    out["funnel"] = {
        "starts": starts,
        "phones": phones,
        "rate": round(100 * phones / starts) if starts else 0,
    }
    return out


# ── Кэш сводок YClients ──────────────────────────────────────────────────────────

def get_yc_cache(phone):
    """Возвращает {'data': json_str, 'age': секунд_с_обновления} или None."""
    return fetchone(
        "SELECT data, EXTRACT(EPOCH FROM (NOW() - updated_at)) AS age "
        "FROM yc_cache WHERE phone = %s",
        (phone,),
    )


def set_yc_cache(phone, data_json):
    """Сохраняет/обновляет сводку YClients по телефону (data_json — строка JSON)."""
    execute(
        "INSERT INTO yc_cache (phone, data, updated_at) VALUES (%s, %s, NOW()) "
        "ON CONFLICT (phone) DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()",
        (phone, data_json),
    )


def get_last_incoming_id():
    """ID последнего ВХОДЯЩЕГО сообщения (для звука/уведомления о новом от клиента)."""
    row = fetchone("SELECT COALESCE(MAX(id),0) AS m FROM messages WHERE direction='in'")
    return int(row["m"] or 0)


def get_last_incoming_message():
    """Последнее входящее сообщение + поля клиента (для всплывающего уведомления)."""
    return fetchone("""
        SELECT m.id, m.client_id, m.text,
               c.reg_last_name, c.reg_first_name, c.reg_patronymic,
               c.first_name, c.last_name, c.patronymic
        FROM messages m JOIN clients c ON c.id = m.client_id
        WHERE m.direction = 'in'
        ORDER BY m.id DESC LIMIT 1
    """)


def get_new_messages_since(last_id):
    return fetchall("""
        SELECT m.id, m.client_id, m.direction, m.text, m.created_at
        FROM messages m WHERE m.id > %s ORDER BY m.id ASC
    """, (last_id,))


# ── MESSAGE TEMPLATES ─────────────────────────────────────────────────────────

def get_message_template(key):
    row = fetchone("SELECT text FROM message_templates WHERE key=%s", (key,))
    return row["text"] if row else None


def get_all_message_templates():
    return fetchall("SELECT key, text FROM message_templates ORDER BY key")


def save_message_template(key, text):
    execute(
        """INSERT INTO message_templates (key, text) VALUES (%s, %s)
           ON CONFLICT (key) DO UPDATE SET text=EXCLUDED.text""",
        (key, text),
    )


# ── ОБНОВЛЕНИЕ КЛИЕНТА ─────────────────────────────────────────────────────────
def update_client(client_id, first_name, last_name, patronymic, phone, notes):
    """
    Обновляет данные клиента. ФИО пишем и в редактируемые поля, и в анкетные
    reg_* — чтобы правка из веба отображалась (приоритет у reg_*).
    """
    execute("""
        UPDATE clients
        SET first_name=%s, last_name=%s, patronymic=%s,
            reg_first_name=%s, reg_last_name=%s, reg_patronymic=%s,
            phone=%s, notes=%s
        WHERE id=%s
    """, (first_name, last_name, patronymic,
          first_name, last_name, patronymic,
          phone, notes, client_id))


# ── ШАБЛОНЫ ЧАТОВ (быстрые ответы) ────────────────────────────────────────────
def get_all_chat_templates():
    return fetchall("SELECT * FROM chat_templates ORDER BY name")

def create_chat_template(name, text):
    row = execute_returning(
        "INSERT INTO chat_templates (name, text) VALUES (%s, %s) RETURNING id",
        (name, text)
    )
    return row["id"] if row else None

def update_chat_template(tpl_id, name, text):
    execute("UPDATE chat_templates SET name=%s, text=%s WHERE id=%s", (name, text, tpl_id))

def delete_chat_template(tpl_id):
    execute("DELETE FROM chat_templates WHERE id=%s", (tpl_id,))


# ── НАПОМИНАНИЯ (дедупликация по id записи YCLIENTS) ───────────────────────────

def yc_reminder_sent(record_id) -> bool:
    row = fetchone("SELECT 1 FROM yc_reminders_sent WHERE record_id=%s", (record_id,))
    return bool(row)


def mark_yc_reminder_sent(record_id):
    execute(
        "INSERT INTO yc_reminders_sent (record_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (record_id,),
    )


def yc_booking_notified(record_id) -> bool:
    row = fetchone("SELECT 1 FROM yc_bookings_notified WHERE record_id=%s", (record_id,))
    return bool(row)


def mark_yc_booking_notified(record_id):
    execute(
        "INSERT INTO yc_bookings_notified (record_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (record_id,),
    )


def yc_booking_seen(record_id) -> bool:
    row = fetchone("SELECT 1 FROM yc_bookings_seen WHERE record_id=%s", (record_id,))
    return bool(row)


def mark_yc_booking_seen(record_id):
    execute(
        "INSERT INTO yc_bookings_seen (record_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (record_id,),
    )


# ── ДЕНЬ РОЖДЕНИЯ ──────────────────────────────────────────────────────────────

def get_birthday_clients_today():
    """Клиенты с реальным Telegram, у кого сегодня день рождения (по birth_date)."""
    return fetchall("""
        SELECT * FROM clients
        WHERE tg_id > 0 AND birth_date IS NOT NULL
          AND EXTRACT(MONTH FROM birth_date) = EXTRACT(MONTH FROM CURRENT_DATE)
          AND EXTRACT(DAY   FROM birth_date) = EXTRACT(DAY   FROM CURRENT_DATE)
    """)


def birthday_already_sent(client_id, year) -> bool:
    row = fetchone(
        "SELECT 1 FROM birthday_sent WHERE client_id=%s AND year=%s",
        (client_id, year),
    )
    return bool(row)


def mark_birthday_sent(client_id, year):
    execute(
        "INSERT INTO birthday_sent (client_id, year) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (client_id, year),
    )


# ── НАСТРОЙКИ БОТА ────────────────────────────────────────────────────────────

def init_bot_settings():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT 'true'
            )
        """)
    defaults = [
        ("birthday_enabled", "false"),   # автопоздравления с ДР: выкл по умолчанию
    ]
    for key, val in defaults:
        try:
            execute(
                "INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (key, val)
            )
        except Exception:
            pass

def get_setting(key: str) -> bool:
    row = fetchone("SELECT value FROM bot_settings WHERE key=%s", (key,))
    return row["value"] == "true" if row else True

def set_setting(key: str, value: bool):
    execute(
        "INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        (key, "true" if value else "false")
    )
