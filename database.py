"""
database.py — слой данных (SQLite, встроенная база, без отдельного сервера).

Файл базы лежит в пользовательской папке (по умолчанию ~/.reform_crm/cosmo.db
или путь из переменной окружения DB_PATH). API функций полностью совпадает с
прежней версией на PostgreSQL — остальной код менять не нужно.
"""

import os
import re
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date, time


# ── Путь к файлу базы ──────────────────────────────────────────────────────────

def _data_dir() -> str:
    d = os.environ.get("REFORM_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".reform_crm")
    os.makedirs(d, exist_ok=True)
    return d

DB_PATH = os.environ.get("DB_PATH") or os.path.join(_data_dir(), "cosmo.db")


# ── Утилита нормализации телефона ─────────────────────────────────────────────

def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return digits


# ── Парсинг дат из текста SQLite в объекты Python ─────────────────────────────

def _parse_dt(s):
    if not s or not isinstance(s, str):
        return s
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            continue
    return None

def _parse_date(s):
    dt = _parse_dt(s)
    return dt.date() if dt else None

def _parse_time(s):
    if not s or not isinstance(s, str):
        return s
    for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s.strip(), fmt).time()
        except ValueError:
            continue
    return None

# Столбцы, которые автоматически превращаем в datetime/date/time при чтении.
_DT_COLS   = {"created_at", "last_message_at", "phone_confirmed_at", "sent_at"}
_DATE_COLS = {"birth_date", "booking_date"}
_TIME_COLS = {"booking_time"}


def _row_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        name = col[0]
        val = row[idx]
        if isinstance(val, str) and val:
            if name in _DT_COLS:
                val = _parse_dt(val)
            elif name in _DATE_COLS:
                val = _parse_date(val)
            elif name in _TIME_COLS:
                val = _parse_time(val)
        d[name] = val
    return d


# sqlite3 не умеет сам сохранять объекты date/datetime — задаём адаптеры.
sqlite3.register_adapter(datetime, lambda v: v.strftime("%Y-%m-%d %H:%M:%S"))
sqlite3.register_adapter(date, lambda v: v.strftime("%Y-%m-%d"))
sqlite3.register_adapter(time, lambda v: v.strftime("%H:%M:%S"))


# ── Соединение ────────────────────────────────────────────────────────────────

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = _row_factory
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=8000")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _prep(sql):
    """PostgreSQL-стиль %s → SQLite ?, и убираем RETURNING (берём lastrowid)."""
    sql = sql.replace("%s", "?")
    sql = re.sub(r"\s+RETURNING\s+\w+", "", sql, flags=re.IGNORECASE)
    return sql


def fetchall(sql, params=()):
    with get_conn() as conn:
        cur = conn.execute(_prep(sql), params)
        return cur.fetchall()


def fetchone(sql, params=()):
    with get_conn() as conn:
        cur = conn.execute(_prep(sql), params)
        return cur.fetchone()


def execute(sql, params=()):
    with get_conn() as conn:
        cur = conn.execute(_prep(sql), params)
        return cur.rowcount


def execute_returning(sql, params=()):
    """Аналог прежнего RETURNING id — возвращает {'id': <новый id>}."""
    with get_conn() as conn:
        cur = conn.execute(_prep(sql), params)
        return {"id": cur.lastrowid}


# ── Инициализация БД ──────────────────────────────────────────────────────────

def init_db():
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id               INTEGER UNIQUE NOT NULL,
                username            TEXT DEFAULT '',
                first_name          TEXT DEFAULT '',
                last_name           TEXT DEFAULT '',
                patronymic          TEXT DEFAULT '',
                phone               TEXT DEFAULT '',
                notes               TEXT DEFAULT '',
                created_at          TIMESTAMP DEFAULT (datetime('now','localtime')),
                phone_confirmed_at  TIMESTAMP,
                unread_count        INTEGER DEFAULT 0
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                direction   TEXT NOT NULL,
                text        TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT (datetime('now','localtime')),
                is_read     INTEGER DEFAULT 0
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id           INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                client_phone        TEXT NOT NULL,
                client_name         TEXT NOT NULL,
                client_first_name   TEXT DEFAULT '',
                client_last_name    TEXT DEFAULT '',
                client_patronymic   TEXT DEFAULT '',
                master_name         TEXT NOT NULL,
                booking_time        TIME NOT NULL,
                booking_date        DATE NOT NULL,
                created_at          TIMESTAMP DEFAULT (datetime('now','localtime')),
                reminder_sent       INTEGER DEFAULT 0,
                status              TEXT DEFAULT 'active'
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS doctors (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name  TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS client_categories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                color      TEXT DEFAULT '#c06090',
                created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
                protected  INTEGER DEFAULT 0
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS client_category_map (
                client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                category_id INTEGER NOT NULL REFERENCES client_categories(id) ON DELETE CASCADE,
                PRIMARY KEY (client_id, category_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS yc_reminders_sent (
                record_id INTEGER PRIMARY KEY,
                sent_at   TIMESTAMP DEFAULT (datetime('now','localtime'))
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS birthday_sent (
                client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                year      INTEGER NOT NULL,
                sent_at   TIMESTAMP DEFAULT (datetime('now','localtime')),
                PRIMARY KEY (client_id, year)
            )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_client  ON messages(client_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_date    ON bookings(booking_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_client  ON bookings(client_id)")

    # Идемпотентные миграции (в SQLite нет ADD COLUMN IF NOT EXISTS — ловим ошибку).
    _safe_alter("ALTER TABLE clients  ADD COLUMN unread_count INTEGER DEFAULT 0")
    _safe_alter("ALTER TABLE messages ADD COLUMN is_read INTEGER DEFAULT 0")
    _safe_alter("ALTER TABLE messages ADD COLUMN media_type TEXT")
    _safe_alter("ALTER TABLE messages ADD COLUMN media_file_id TEXT")
    _safe_alter("ALTER TABLE messages ADD COLUMN media_filename TEXT")
    _safe_alter("ALTER TABLE messages ADD COLUMN media_local_path TEXT")
    _safe_alter("ALTER TABLE bookings ADD COLUMN reminder_sent INTEGER DEFAULT 0")
    _safe_alter("ALTER TABLE bookings ADD COLUMN status TEXT DEFAULT 'active'")
    _safe_alter("ALTER TABLE clients ADD COLUMN patronymic TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE bookings ADD COLUMN client_first_name TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE bookings ADD COLUMN client_last_name TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE bookings ADD COLUMN client_patronymic TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE clients ADD COLUMN reg_last_name  TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE clients ADD COLUMN reg_first_name TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE clients ADD COLUMN reg_patronymic TEXT DEFAULT ''")
    _safe_alter("ALTER TABLE clients ADD COLUMN birth_date DATE")
    _safe_alter("ALTER TABLE client_categories ADD COLUMN protected INTEGER DEFAULT 0")

    _init_message_templates()
    init_bot_settings()
    ensure_vip_category()   # защищённая категория «VIP» (нельзя удалить)

    print("✅ DB initialized:", DB_PATH)


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
                "INSERT INTO message_templates (key, text) VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
                (key, meta["text"]),
            )


def _safe_alter(sql):
    try:
        with get_conn() as conn:
            conn.execute(sql)
    except Exception:
        pass  # столбец уже есть — это нормально


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
        "INSERT INTO clients (tg_id,username,first_name,last_name,patronymic) VALUES (%s,%s,%s,%s,%s)",
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
        "UPDATE clients SET phone=%s, phone_confirmed_at=datetime('now','localtime') WHERE tg_id=%s",
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
    rows = fetchall("""
        SELECT c.*,
               (SELECT text       FROM messages WHERE client_id=c.id ORDER BY datetime(created_at) DESC, id DESC LIMIT 1) AS last_message,
               (SELECT created_at FROM messages WHERE client_id=c.id ORDER BY datetime(created_at) DESC, id DESC LIMIT 1) AS last_message_at,
               COALESCE(
                 (SELECT json_group_array(json_object('id', cc.id, 'name', cc.name, 'color', cc.color))
                  FROM client_categories cc
                  JOIN client_category_map ccm ON ccm.category_id = cc.id
                  WHERE ccm.client_id = c.id), '[]'
               ) AS categories
        FROM clients c
        ORDER BY last_message_at DESC, c.created_at DESC
    """)
    for r in rows:
        cats = r.get("categories")
        if isinstance(cats, str):
            try:
                r["categories"] = json.loads(cats)
            except Exception:
                r["categories"] = []
    return rows


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
            "INSERT INTO client_categories (name, color) VALUES (%s, %s)",
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
        execute("UPDATE client_categories SET protected=1 WHERE id=%s", (row["id"],))
        return row["id"]
    row = execute_returning(
        "INSERT INTO client_categories (name, color, protected) VALUES (%s, %s, 1)",
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


# ── MESSAGES ──────────────────────────────────────────────────────────────────

def save_message(client_id, direction, text, media_type=None, media_file_id=None,
                 media_filename=None, media_local_path=None):
    execute(
        """INSERT INTO messages
           (client_id, direction, text, media_type, media_file_id, media_filename, media_local_path)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (client_id, direction, text, media_type, media_file_id, media_filename, media_local_path),
    )
    if direction == "in":
        execute("UPDATE clients SET unread_count = unread_count + 1 WHERE id=%s", (client_id,))


def get_message(message_id):
    return fetchone("SELECT * FROM messages WHERE id=%s", (message_id,))


def get_messages(client_id):
    return fetchall(
        "SELECT * FROM messages WHERE client_id=%s ORDER BY datetime(created_at) ASC, id ASC",
        (client_id,),
    )


def get_messages_since(client_id, after_id=0):
    return fetchall(
        "SELECT * FROM messages WHERE client_id=%s AND id > %s ORDER BY id ASC",
        (client_id, after_id),
    )


def mark_messages_read(client_id):
    execute("UPDATE messages SET is_read=1 WHERE client_id=%s AND direction='in'", (client_id,))
    execute("UPDATE clients SET unread_count=0 WHERE id=%s", (client_id,))


def get_total_unread():
    row = fetchone("SELECT COALESCE(SUM(unread_count),0) AS n FROM clients")
    return int(row["n"])


def get_unread_summary():
    """Сводка непрочитанного: число диалогов с непрочитанными и общее число."""
    row = fetchone(
        "SELECT SUM(CASE WHEN unread_count > 0 THEN 1 ELSE 0 END) AS dialogs, "
        "COALESCE(SUM(unread_count),0) AS total FROM clients"
    )
    return {"dialogs": int(row["dialogs"] or 0), "total": int(row["total"] or 0)}


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
           ON CONFLICT (key) DO UPDATE SET text=excluded.text""",
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
        "INSERT INTO chat_templates (name, text) VALUES (%s, %s)",
        (name, text)
    )
    return row["id"] if row else None

def update_chat_template(tpl_id, name, text):
    execute("UPDATE chat_templates SET name=%s, text=%s WHERE id=%s", (name, text, tpl_id))

def delete_chat_template(tpl_id):
    execute("DELETE FROM chat_templates WHERE id=%s", (tpl_id,))


# ── НАПОМИНАНИЯ (дедупликация по id записи YCLIENTS) ───────────────────────────

def yc_reminder_sent(record_id) -> bool:
    row = fetchone("SELECT 1 AS x FROM yc_reminders_sent WHERE record_id=%s", (record_id,))
    return bool(row)


def mark_yc_reminder_sent(record_id):
    execute(
        "INSERT INTO yc_reminders_sent (record_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (record_id,),
    )


# ── ДЕНЬ РОЖДЕНИЯ ──────────────────────────────────────────────────────────────

def get_birthday_clients_today():
    """Клиенты с реальным Telegram, у кого сегодня день рождения (по birth_date)."""
    return fetchall("""
        SELECT * FROM clients
        WHERE tg_id > 0 AND birth_date IS NOT NULL AND birth_date <> ''
          AND strftime('%m', birth_date) = strftime('%m', 'now', 'localtime')
          AND strftime('%d', birth_date) = strftime('%d', 'now', 'localtime')
    """)


def birthday_already_sent(client_id, year) -> bool:
    row = fetchone(
        "SELECT 1 AS x FROM birthday_sent WHERE client_id=%s AND year=%s",
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
        conn.execute("""
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
        "INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=excluded.value",
        (key, "true" if value else "false")
    )
