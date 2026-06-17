"""
Шаблоны сообщений с ключевыми словами: {ИМЯ}, {ФАМИЛИЯ}, {ОТЧЕСТВО}, {ФИО}, {ДАТА}, {ВРЕМЯ}, {ВРАЧ}
"""

import database as db

DEFAULT_TEMPLATES = {
    "booking_created": {
        "label": "Уведомление о новой записи (клиенту)",
        "text": (
            "✅ *Вы записаны!*\n\n"
            "{ИМЯ}, вы записаны:\n\n"
            "📅 Дата: *{ДАТА}*\n"
            "🕐 Время: *{ВРЕМЯ}*\n"
            "👩‍⚕️ Врач: *{ВРАЧ}*\n\n"
            "Ждём вас в клинике! 🌸"
        ),
        "hint": "Ключи: {ИМЯ}, {ФАМИЛИЯ}, {ОТЧЕСТВО}, {ФИО}, {ДАТА}, {ВРЕМЯ}, {ВРАЧ}",
    },
    "booking_created_log": {
        "label": "Запись в чат (системное сообщение)",
        "text": "✅ Запись создана: {ДАТА} в {ВРЕМЯ}, врач: {ВРАЧ}",
        "hint": "Ключи: {ДАТА}, {ВРЕМЯ}, {ВРАЧ}, {ФИО}, {ИМЯ}",
    },
    "client_profile": {
    "label": "Профиль клиента (кнопка «Мой профиль»)",
    "text": (
        "👤 *{ИМЯ}*\n"
        "📱 {ТЕЛЕФОН}\n\n"
        "{ЗАПИСИ}"
    ),
    "hint": "Ключи: {ИМЯ}, {ТЕЛЕФОН}, {ЗАПИСИ} — список предстоящих записей",
    },
    "booking_reschedule": {
        "label": "Уведомление о переносе (клиенту)",
        "text": (
            "🔄 *Ваша запись перенесена*\n\n"
            "{ИМЯ}, ваша запись обновлена:\n\n"
            "📅 Новая дата: *{ДАТА}*\n"
            "🕐 Новое время: *{ВРЕМЯ}*\n"
            "👩‍⚕️ Врач: *{ВРАЧ}*\n\n"
            "Ждём вас в клинике! 🌸"
        ),
        "hint": "Ключи: {ИМЯ}, {ФАМИЛИЯ}, {ОТЧЕСТВО}, {ФИО}, {ДАТА}, {ВРЕМЯ}, {ВРАЧ}",
    },
    "booking_reschedule_log": {
        "label": "Перенос в чат (системное сообщение)",
        "text": "🔄 Запись перенесена на {ДАТА} в {ВРЕМЯ}",
        "hint": "Ключи: {ДАТА}, {ВРЕМЯ}, {ВРАЧ}, {ФИО}",
    },
    "booking_cancel": {
        "label": "Уведомление об отмене (клиенту)",
        "text": (
            "❌ {ИМЯ}, ваша запись на *{ДАТА}* в *{ВРЕМЯ}* была отменена.\n\n"
            "Если это ошибка — свяжитесь с нами."
        ),
        "hint": "Ключи: {ИМЯ}, {ДАТА}, {ВРЕМЯ}, {ВРАЧ}, {ФИО}",
    },
    "booking_cancel_log": {
        "label": "Отмена в чат (системное сообщение)",
        "text": "❌ Запись отменена: {ДАТА} в {ВРЕМЯ}",
        "hint": "Ключи: {ДАТА}, {ВРЕМЯ}, {ВРАЧ}, {ФИО}",
    },
    "reminder": {
        "label": "Напоминание о визите (за сутки)",
        "text": (
            "{ИМЯ}, добрый день! 🤍\n\n"
            "Напоминаем, что завтра в {ВРЕМЯ} у Вас запланирован визит к нашему "
            "врачу-косметологу — {ВРАЧ}🥰✨\n\n"
            "Пожалуйста, подтвердите свою запись, нажав на кнопку ниже."
        ),
        "hint": "Ключи: {ИМЯ}, {ДАТА}, {ВРЕМЯ}, {ВРАЧ}, {ФИО}",
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
    "bot_confirm": {
        "label": "Подтверждение визита (после кнопки «Подтвердить»)",
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
        "hint": "Без ключей",
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


def render_template(key: str, booking: dict = None, **extra) -> str:
    tpl = db.get_message_template(key)
    if not tpl:
        tpl = DEFAULT_TEMPLATES.get(key, {}).get("text", "")
    ctx = {}
    if booking:
        ctx.update(booking_context(booking, date=extra.pop("date", None), time=extra.pop("time", None)))
    ctx.update(extra)
    text = tpl
    for k, v in ctx.items():
        text = text.replace("{" + k + "}", str(v))
    return text


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
