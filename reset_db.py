"""
reset_db.py — очистка базы (SQLite) для новых тестов.

Удаляет данные (клиентов и каскадно их сообщения/брони/категории-привязки),
но НЕ трогает структуру таблиц и справочники (категории, шаблоны, настройки).

⚠️  ЭТО НЕОБРАТИМО. Запускать осознанно и при ОСТАНОВЛЕННОМ приложении.

Использование:
    python reset_db.py            # показать, что в базе (ничего не удаляет)
    python reset_db.py --yes      # реально очистить
"""

import sys
import database as db

TABLES = ["clients", "messages", "bookings", "client_category_map",
          "birthday_sent", "yc_reminders_sent"]


def _counts():
    res = {}
    for t in TABLES:
        try:
            row = db.fetchone(f"SELECT COUNT(*) AS n FROM {t}")
            res[t] = row["n"] if row else 0
        except Exception as e:
            res[t] = f"нет таблицы? ({e})"
    return res


def main():
    do_it = "--yes" in sys.argv
    print("Файл базы:", db.DB_PATH)
    print("Текущее состояние:")
    for t, n in _counts().items():
        print(f"  {t:22} {n}")

    if not do_it:
        print("\nЭто предпросмотр. Ничего не удалено.")
        print("Чтобы реально очистить — запусти:  python reset_db.py --yes")
        return

    print("\n⚠️  Очищаю базу...")
    with db.get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        # удаление клиентов каскадно зачистит messages/bookings/привязки/ДР
        conn.execute("DELETE FROM clients")
        conn.execute("DELETE FROM yc_reminders_sent")
        # сбросить счётчики автоинкремента
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN "
                         "('clients','messages','bookings')")
        except Exception:
            pass

    print("Готово. Состояние после очистки:")
    for t, n in _counts().items():
        print(f"  {t:22} {n}")
    print("\n✅ База очищена.")


if __name__ == "__main__":
    main()
