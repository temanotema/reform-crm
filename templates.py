"""
Шаблоны сообщений с ключевыми словами: {ИМЯ}, {ФАМИЛИЯ}, {ОТЧЕСТВО}, {ФИО}, {ДАТА}, {ВРЕМЯ}, {ВРАЧ}
Премиум-эмодзи: {эмодзи:ключ} (см. EMOJI_MAP) или {эмодзи:<числовой_id>}.
Тексты рендерятся в HTML (*жирный* → <b>жирный</b>), поэтому бот отправляет их с parse_mode="HTML".
"""

import html
import json
import os
import re

import database as db

# Запасные символы для эмодзи по id (из выгруженного пака emoji_pack/manifest.json),
# чтобы у не-премиум пользователей вместо кастомного эмодзи стоял его базовый символ.
_PACK_FALLBACK = {}
try:
    _mpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emoji_pack", "manifest.json")
    if os.path.exists(_mpath):
        with open(_mpath, encoding="utf-8") as _mf:
            for _e in json.load(_mf):
                if _e.get("id"):
                    _PACK_FALLBACK[str(_e["id"])] = _e.get("emoji") or "⭐"
except Exception:
    _PACK_FALLBACK = {}

# ── Премиум-эмодзи: дружелюбный ключ → (custom_emoji_id, запасной символ) ──────
# id получены через get_emoji_id.py. Чтобы добавить новый — допиши строку сюда.
EMOJI_MAP = {
    "сердце": ("5258179403652801593", "❤️"),
    "люди":   ("5258513401784573443", "👥"),
    "вправо": ("5260450573768990626", "➡️"),
    "влево":  ("5258236805890710909", "⬅️"),
}

# Текстовые ключи для шпаргалки на вебе: (плейсхолдер, пояснение).
PLACEHOLDER_KEYS = [
    ("{ИМЯ}",              "Имя клиента"),
    ("{ФАМИЛИЯ}",          "Фамилия"),
    ("{ОТЧЕСТВО}",         "Отчество"),
    ("{ФИО}",              "Фамилия Имя Отчество"),
    ("{ДАТА}",             "Дата записи"),
    ("{ВРЕМЯ}",            "Время записи"),
    ("{ВРАЧ}",             "Имя врача"),
    ("{ТЕЛЕФОН}",          "Телефон клиента"),
    ("{ЗАПИСИ}",           "Список предстоящих записей"),
    ("{КЛИНИКА}",          "Название клиники"),
    ("{ПРИВЕТСТВИЕ}",      "«Доброе утро/день/вечер» по времени"),
    ("{ДНЕЙ_С_НАМИ}",      "Сколько клиент с нами"),
    ("{ПОСЛЕДНИЙ_ВИЗИТ}",  "Дата последнего визита"),
    ("{БЛИЖАЙШАЯ_ЗАПИСЬ}", "Дата ближайшей записи"),
]

_EMOJI_RE = re.compile(r"\{эмодзи:([^}]+)\}", re.IGNORECASE)
_BOLD_RE = re.compile(r"\*(.+?)\*", re.S)
_M0, _M1 = "", ""  # служебные маркеры (в обычном тексте не встречаются)


def _emoji_tag(m):
    key = m.group(1).strip()
    ent = EMOJI_MAP.get(key.lower())
    if ent:
        cid, fb = ent
    elif key.isdigit():
        cid, fb = key, _PACK_FALLBACK.get(key, "⭐")
    else:
        return m.group(0)  # неизвестный ключ — оставить как есть
    return f'<tg-emoji emoji-id="{cid}">{fb}</tg-emoji>'


def _md_to_html(text: str) -> str:
    """Текст шаблона → безопасный HTML для Telegram.
    Порядок важен: сначала экранируем (html.escape не трогает фигурные скобки),
    затем *жирный* → <b>, и в самом конце вставляем теги эмодзи, чтобы их
    угловые скобки не были экранированы."""
    text = html.escape(text, quote=False)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _EMOJI_RE.sub(_emoji_tag, text)
    return text

DEFAULT_TEMPLATES = {
    "booking_created": {
        "label": "Уведомление о новой записи (клиенту)",
        "text": (
            "{ИМЯ}, Вы записаны в Re.form cosmetology 🫶\n\n"
            "❗ Важно! ❗ Обращаем ваше внимание, что наша клиника ведёт приём по новому адресу:\n"
            "📍 Садовническая ул., 14с1 🚇 м. Новокузнецкая\n\n"
            "📅 {ДАТА} в {ВРЕМЯ}\n"
            "👩‍⚕️ Специалист: {ВРАЧ}\n\n"
            "🤍 Будем рады видеть вас в нашей клинике!"
        ),
        "hint": "Отправляется один раз после появления записи в YClients (кроме очереди/листа ожидания). "
                "Ключи: {ИМЯ}, {ДАТА}, {ВРЕМЯ}, {ВРАЧ}",
    },
    "reminder": {
        "label": "Подтверждение записи (кнопки ДА/НЕТ)",
        "text": (
            "{ИМЯ}, добрый день! 🤍\n\n"
            "Напоминаем, что {КОГДА} в {ВРЕМЯ} у Вас запланирован визит к нашему "
            "врачу-косметологу — {ВРАЧ}🥰✨\n\n"
            "Пожалуйста, подтвердите свою запись, нажав на кнопку ниже."
        ),
        "hint": "Ключи: {ИМЯ}, {КОГДА} (сегодня/завтра/дата), {ДАТА}, {ВРЕМЯ}, {ВРАЧ}, {ФИО}",
    },
    "birthday": {
        "label": "Поздравление с днём рождения",
        "text": (
            "{ИМЯ}, от всей души поздравляем Вас с днём рождения! 🤍\n\n"
            "Желаем красоты, здоровья и прекрасного настроения. "
            "Будем рады видеть Вас в Re.form Cosmetology 🌸"
        ),
        "hint": "Ключи: {ИМЯ}, {ФИО}",
    },

    # ── Тексты, которые бот шлёт клиенту (вкладка «Шаблоны») ──────────────────
    "bot_welcome": {
        "label": "Приветствие нового клиента (запрос номера)",
        "text": (
            "👋 Добро пожаловать в {КЛИНИКА}!\n\n"
            "Нажмите кнопку ниже, чтобы поделиться номером — мы сможем показать "
            "ваш профиль и быть на связи 🌸"
        ),
        "hint": "Ключи: {КЛИНИКА}",
    },
    "bot_after_phone": {
        "label": "Сообщение после подтверждения номера",
        "text": (
            "Добрый день! 🌸\n\n"
            "Меня зовут Анна, я администратор клиники {КЛИНИКА}.\n\n"
            "Спасибо, что подтвердили свой номер. Мы всегда на связи — "
            "задавайте любые вопросы, и я отвечу в ближайшее время!"
        ),
        "hint": "Ключи: {КЛИНИКА}",
    },
    "bot_reg_start": {
        "label": "Регистрация — запрос фамилии",
        "text": "Спасибо! Давайте знакомиться 🤍\n\nВведите, пожалуйста, вашу фамилию:",
        "hint": "Без ключей",
    },
    "bot_reg_firstname": {
        "label": "Регистрация — запрос имени",
        "text": "Принято 🤍\n\nТеперь введите имя:",
        "hint": "Ключи: {ФАМИЛИЯ}",
    },
    "bot_reg_patronymic": {
        "label": "Регистрация — запрос отчества",
        "text": "Введите отчество или нажмите «Пропустить», если его нет:",
        "hint": "Без ключей",
    },
    "bot_reg_birth": {
        "label": "Регистрация — запрос даты рождения",
        "text": "И последнее — дата рождения в формате ДД.ММ.ГГГГ.\nНапример: 15.06.1995",
        "hint": "Без ключей",
    },
    "bot_reg_done": {
        "label": "Регистрация — завершение",
        "text": "Готово, благодарим! 🤍\n\n{ФИО}\nДата рождения: {ДАТА}",
        "hint": "Ключи: {ФИО}, {ИМЯ}, {ДАТА}",
    },
    "bot_profile_caption": {
        "label": "Подпись под карточкой «Мой профиль»",
        "text": (
            "{ПРИВЕТСТВИЕ}, {ИМЯ}.\n"
            "{ДНЕЙ_С_НАМИ} ❤️\n"
            "Последний визит: {ПОСЛЕДНИЙ_ВИЗИТ}\n"
            "📅 Ближайшая запись: {БЛИЖАЙШАЯ_ЗАПИСЬ}"
        ),
        "hint": "Ключи: {ПРИВЕТСТВИЕ}, {ИМЯ}, {ДНЕЙ_С_НАМИ}, {ПОСЛЕДНИЙ_ВИЗИТ}, {БЛИЖАЙШАЯ_ЗАПИСЬ}. "
                "Строки с пустыми значениями (нет визита/стажа) скрываются автоматически.",
    },
    "bot_confirm_first": {
        "label": "Подтверждение визита — ПЕРВИЧНЫЙ приём (со схемой прохода)",
        "text": (
            "Благодарим за подтверждение!\n\n"
            "‼️ВАЖНО‼️ Наша клиника ведёт приём по новому адресу.\n"
            "Будем рады видеть Вас по адресу:\n"
            "📍 Садовническая улица 14с1\n"
            "🚇 м. Новокузнецкая\n\n"
            "Обращаем внимание: при опоздании более чем на 15 минут, мы, к сожалению, "
            "не сможем провести приём и будем вынуждены его отменить🙏\n\n"
            "✔️ Если Вы посещаете нашу клинику впервые, пожалуйста, подойдите за 15 минут "
            "до визита и возьмите с собой паспорт для оформления первичной документации.\n\n"
            "До скорой встречи,\nRe.form cosmetology 🫶"
        ),
        "hint": "Отправляется при подтверждении записи, если у клиента НЕТ прошлых визитов. "
                "К сообщению автоматически прикрепляется схема прохода. Без ключей.",
    },
    "bot_confirm_repeat": {
        "label": "Подтверждение визита — ПОВТОРНЫЙ приём (с бонусом, со схемой)",
        "text": (
            "Благодарим за подтверждение!\n\n"
            "‼️ВАЖНО‼️ Наша клиника ведёт приём по новому адресу.\n"
            "Будем рады видеть Вас по адресу:\n"
            "📍 Садовническая улица 14с1\n"
            "🚇 м. Новокузнецкая\n\n"
            "Обращаем внимание: при опоздании более чем на 15 минут, мы, к сожалению, "
            "не сможем провести приём и будем вынуждены его отменить🙏\n\n"
            "✨ И приятный бонус: при оплате наличными мы возвращаем Вам 5% от суммы на "
            "бонусный счёт — пусть Ваши будущие процедуры будут ещё приятнее! 🎁\n\n"
            "✔️ Если Вы посещаете нашу клинику впервые, пожалуйста, подойдите за 15 минут "
            "до визита и возьмите с собой паспорт для оформления первичной документации.\n\n"
            "До скорой встречи,\nRe.form cosmetology 🫶"
        ),
        "hint": "Отправляется при подтверждении записи, если у клиента ЕСТЬ прошлые визиты. "
                "К сообщению автоматически прикрепляется схема прохода. Без ключей.",
    },
    "bot_contacts": {
        "label": "Контакты (кнопка «Контакты», со схемой прохода)",
        "text": (
            "📍 *Контакты Re.form Cosmetology*\n\n"
            "Остались вопросы? Напишите нам прямо здесь, в этом чате — администратор "
            "на связи и поможет с записью, расскажет о процедурах, ценах и подберёт "
            "удобное время. 💬\n\n"
            "Мы всегда рядом:\n\n"
            "📌 Адрес: Москва, Садовническая улица, 14с1\n"
            "🗺 На карте: https://yandex.ru/maps/org/re_form_cosmetology/174195752132/\n\n"
            "📞 Телефон: +7 917 590-20-24\n"
            "✉️ Почта: re.form.cosmetology1@gmail.com\n\n"
            "Будем рады видеть вас! ✨"
        ),
        "hint": "Ответ на кнопку «Контакты». Прикрепляется схема прохода (assets/scheme.jpg). Без ключей.",
    },
}


def format_full_name(last_name="", first_name="", patronymic=""):
    parts = [p.strip() for p in (last_name, first_name, patronymic) if p and p.strip()]
    return " ".join(parts)


def booking_name_fields(booking: dict) -> dict:
    """Извлекает имя/фамилию/отчество из записи (с fallback на client_name)."""
    first = (booking.get("client_first_name") or "").strip()
    last = (booking.get("client_last_name") or "").strip()
    patron = (booking.get("client_patronymic") or "").strip()

    if not first and not last and booking.get("client_name"):
        parts = booking["client_name"].strip().split()
        if len(parts) >= 3:
            last, first, patron = parts[0], parts[1], " ".join(parts[2:])
        elif len(parts) == 2:
            last, first = parts[0], parts[1]
        elif len(parts) == 1:
            first = parts[0]

    if not first and booking.get("first_name"):
        first = booking["first_name"]
    if not last and booking.get("last_name"):
        last = booking["last_name"]
    if not patron and booking.get("patronymic"):
        patron = booking["patronymic"]

    fio = format_full_name(last, first, patron) or booking.get("client_name", "")
    display_first = first or (fio.split()[1] if len(fio.split()) > 1 else fio.split()[0] if fio else "Уважаемый гость")

    return {
        "ИМЯ": display_first,
        "ФАМИЛИЯ": last,
        "ОТЧЕСТВО": patron,
        "ФИО": fio,
    }


def booking_context(booking: dict, date=None, time=None) -> dict:
    ctx = booking_name_fields(booking)
    d = date or booking.get("booking_date")
    t = time or booking.get("booking_time")
    ctx["ДАТА"] = d.strftime("%d.%m.%Y") if d else ""
    ctx["ВРЕМЯ"] = t.strftime("%H:%M") if t else ""
    ctx["ВРАЧ"] = booking.get("master_name", "")
    return ctx


def _global_ctx() -> dict:
    """Ключи, доступные в ЛЮБОМ шаблоне (не зависят от клиента/записи)."""
    try:
        from config import CLINIC_NAME
    except Exception:
        CLINIC_NAME = ""
    try:
        from visual.texts import greeting_by_hour
        greeting = greeting_by_hour()
    except Exception:
        greeting = "Здравствуйте"
    return {"КЛИНИКА": CLINIC_NAME, "ПРИВЕТСТВИЕ": greeting}


# Незаполненный ключ вида {ВРАЧ}: убираем, чтобы клиент не видел «сырой» текст.
_LEFTOVER_RE = re.compile(r"\{[А-ЯЁA-Z_]{2,}\}")


def render_template(key: str, booking: dict = None, **extra) -> str:
    tpl = db.get_message_template(key)
    if not tpl:
        tpl = DEFAULT_TEMPLATES.get(key, {}).get("text", "")
    ctx = _global_ctx()
    if booking:
        ctx.update(booking_context(booking, date=extra.pop("date", None), time=extra.pop("time", None)))
    ctx.update(extra)
    text = tpl
    for k, v in ctx.items():
        text = text.replace("{" + k + "}", str(v))
    text = _LEFTOVER_RE.sub("", text)
    return _md_to_html(text)


def get_all_templates_for_ui():
    result = []
    for key, meta in DEFAULT_TEMPLATES.items():
        result.append({
            "key": key,
            "label": meta["label"],
            "hint": meta.get("hint", ""),
            "text": db.get_message_template(key) or meta["text"],
            "default": meta["text"],
        })
    return result
