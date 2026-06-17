"""
visual/emoji_manager.py — централизованный реестр эмодзи.

ЗАЧЕМ:
  Чтобы не разбрасывать эмодзи по всему проекту. Любой значок берётся
  по ключу: emoji("profile"). Меняешь значок в одном месте — он меняется
  везде, где используется.

КАК ЭТО РАБОТАЕТ:
  По умолчанию возвращается обычный Unicode-эмодзи (fallback) — он виден
  всем пользователям, включая не-premium (а это 95% аудитории клиники).

  Кастомные Telegram Emoji (анимированные, из премиум-наборов) отображаются
  только у premium-пользователей и требуют отправки через HTML-тег
  <tg-emoji emoji-id="...">. Поэтому они выключены по умолчанию
  (USE_CUSTOM_EMOJI = False). Когда захочешь их включить:
     1) поставь USE_CUSTOM_EMOJI = True;
     2) впиши реальные emoji_id в CUSTOM_EMOJIS;
     3) отправляй сообщение с parse_mode="HTML" и используй emoji_html(key).
  Если у пользователя нет premium — Telegram сам покажет fallback-символ
  из тега, так что ничего не сломается.
"""

# Глобальный переключатель кастомных эмодзи.
# False — везде используются обычные Unicode-символы (рекомендуется сейчас).
USE_CUSTOM_EMOJI = False


# Реестр. Для каждого ключа:
#   "fallback"  — обычный Unicode-эмодзи (показывается всегда);
#   "custom_id" — id кастомного Telegram-эмодзи (заполни, если включишь premium).
CUSTOM_EMOJIS = {
    "profile":      {"fallback": "🪞", "custom_id": ""},
    "appointments": {"fallback": "📅", "custom_id": ""},
    "contact":      {"fallback": "📱", "custom_id": ""},
    "promotions":   {"fallback": "✨", "custom_id": ""},
    "clinic":       {"fallback": "🤍", "custom_id": ""},
    "location":     {"fallback": "📍", "custom_id": ""},
    "doctor":       {"fallback": "👩‍⚕️", "custom_id": ""},
    "time":         {"fallback": "🕐", "custom_id": ""},
    "ok":           {"fallback": "✅", "custom_id": ""},
    "cancel":       {"fallback": "❌", "custom_id": ""},
    "broadcast":    {"fallback": "📢", "custom_id": ""},
    "wave":         {"fallback": "👋", "custom_id": ""},
}


def emoji(key: str) -> str:
    """
    Возвращает Unicode-символ эмодзи по ключу.
    Если ключа нет в реестре — вернёт пустую строку (ничего не сломается).
    Используй это почти везде: emoji("profile"), emoji("ok") и т.д.
    """
    item = CUSTOM_EMOJIS.get(key)
    if not item:
        return ""
    return item["fallback"]


def emoji_html(key: str) -> str:
    """
    HTML-представление эмодзи для отправки с parse_mode="HTML".

    Если USE_CUSTOM_EMOJI=True и у ключа задан custom_id — вернёт
    <tg-emoji emoji-id="...">fallback</tg-emoji> (premium увидят кастомный,
    остальные — fallback). Иначе вернёт обычный Unicode-символ.
    """
    item = CUSTOM_EMOJIS.get(key)
    if not item:
        return ""
    if USE_CUSTOM_EMOJI and item.get("custom_id"):
        return f'<tg-emoji emoji-id="{item["custom_id"]}">{item["fallback"]}</tg-emoji>'
    return item["fallback"]
