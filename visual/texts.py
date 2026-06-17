"""
visual/texts.py — текстовые хелперы для премиального оформления.

Здесь НЕТ бизнес-логики. Только функции, которые помогают формировать
аккуратные, спокойные формулировки. Тексты самих сообщений бота при этом
остаются в bot.py и config.py — их ты редактируешь сам (см. TEXTS_FAQ.md).
"""

from datetime import datetime, timezone, timedelta

# Часовой пояс клиники (Москва, UTC+3). Используем его, чтобы приветствие
# по времени суток было корректным независимо от часового пояса сервера.
CLINIC_TZ = timezone(timedelta(hours=3))


def clinic_now() -> datetime:
    """Текущее время в часовом поясе клиники (МСК)."""
    return datetime.now(CLINIC_TZ)


def greeting_by_hour(hour: int = None) -> str:
    """
    Приветствие по времени суток.

      05:00–11:59 — «Доброе утро»
      12:00–16:59 — «Добрый день»
      17:00–22:59 — «Добрый вечер»
      23:00–04:59 — «Доброй ночи»

    Если hour не передан — берётся текущий час по МСК.
    """
    if hour is None:
        hour = clinic_now().hour
    if 5 <= hour < 12:
        return "Доброе утро"
    if 12 <= hour < 17:
        return "Добрый день"
    if 17 <= hour < 23:
        return "Добрый вечер"
    return "Доброй ночи"


def first_name(full_name: str, default: str = "") -> str:
    """
    Возвращает имя (первое слово) из строки ФИО.
    Безопасно обрабатывает пустые значения.
    """
    if not full_name:
        return default
    parts = full_name.strip().split()
    return parts[0] if parts else default
