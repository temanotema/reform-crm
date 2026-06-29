"""
bot.py — Telegram-бот клиники Re.form.

Назначение: поддержка клиентов + карточка профиля (данные из YCLIENTS) +
регистрация клиента (ФИО + дата рождения) после подтверждения номера.
Создание записей в боте УБРАНО — запись ведётся в YCLIENTS.
"""

import os
import logging
import asyncio
from io import BytesIO
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

import database as db
from config import BOT_TOKEN, ADMIN_IDS, CLINIC_NAME, TELEGRAM_PROXY

# ── Визуальный слой ──────────────────────────────────────────────────────────
from visual.profile_card import render_profile_card
from visual.texts import greeting_by_hour, clinic_now

# ── Шаблоны сообщений (напоминание/поздравление, подстановка {ИМЯ} и т.п.) ─────
from templates import render_template

# ── Интеграция с YCLIENTS ─────────────────────────────────────────────────────
import yclients

# ── Web Push (уведомления админам на телефон) ─────────────────────────────────
import webpush

logger = logging.getLogger(__name__)

# Если задан TELEGRAM_PROXY — весь трафик бота к api.telegram.org идёт через него
# (для socks5:// нужен пакет aiohttp-socks, см. requirements.txt).
if TELEGRAM_PROXY:
    from aiogram.client.session.aiohttp import AiohttpSession
    bot = Bot(token=BOT_TOKEN, session=AiohttpSession(proxy=TELEGRAM_PROXY))
    logger.info("Бот ходит в Telegram через прокси")
else:
    bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ── FSM ────────────────────────────────────────────────────────────────────────

class BroadcastForm(StatesGroup):
    waiting_for_text    = State()
    waiting_for_confirm = State()


class RegForm(StatesGroup):
    """Анкета клиента после подтверждения номера."""
    last_name  = State()
    first_name = State()
    patronymic = State()
    birth      = State()


class TestUserForm(StatesGroup):
    """Тестовая команда /newuser (только для админа): ручной ввод телефона и ФИО."""
    phone = State()
    fio   = State()


# ── Клавиатуры ────────────────────────────────────────────────────────────────

PROFILE_BUTTON = "Мой профиль"
DOCTORS_BUTTON = "Наши врачи"
CONTACTS_BUTTON = "Контакты"
SKIP_BUTTON    = "Пропустить"

# ID премиум-эмодзи (получены через get_emoji_id.py) — иконки на кнопках (Bot API 9.4).
EMOJI_PROFILE     = "5258179403652801593"  # ❤️
EMOJI_DOCTORS     = "5258513401784573443"  # 👥
EMOJI_ARROW_RIGHT = "5260450573768990626"  # ➡️
EMOJI_ARROW_LEFT  = "5258236805890710909"  # ⬅️
_INVIS = "⠀"  # невидимый символ: кнопка-стрелка показывает только эмодзи-иконку

# Папка с фотографиями врачей (заполняется скриптом seed_doctors.py).
DOCTORS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "doctors_photos")

# Схема прохода — прикрепляется к «Контактам» и к подтверждению записи.
SCHEME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "scheme.jpg")


def _kbtn(text, emoji_id=None, style=None, **extra):
    """KeyboardButton с премиум-иконкой/цветом (Bot API 9.4). На старом aiogram
    тихо откатывается к обычной кнопке."""
    kw = {"text": text, **extra}
    if emoji_id:
        kw["icon_custom_emoji_id"] = emoji_id
    if style:
        kw["style"] = style
    try:
        return types.KeyboardButton(**kw)
    except Exception:
        return types.KeyboardButton(text=text, **extra)


def _ikbtn(text, callback_data, emoji_id=None, style=None):
    """InlineKeyboardButton с премиум-иконкой/цветом, с откатом на старом aiogram."""
    kw = {"text": text, "callback_data": callback_data}
    if emoji_id:
        kw["icon_custom_emoji_id"] = emoji_id
    if style:
        kw["style"] = style
    try:
        return types.InlineKeyboardButton(**kw)
    except Exception:
        return types.InlineKeyboardButton(text=text, callback_data=callback_data)


def _name_ctx(client=None, user=None) -> dict:
    """Подстановки про клиента ({ИМЯ}/{ФАМИЛИЯ}/{ОТЧЕСТВО}/{ФИО}/{ТЕЛЕФОН})
    для шаблонов. Имя берём из анкеты, иначе из Telegram."""
    first = last = patr = phone = ""
    if client:
        first = client.get("reg_first_name") or client.get("first_name") or ""
        last = client.get("reg_last_name") or client.get("last_name") or ""
        patr = client.get("reg_patronymic") or ""
        phone = client.get("phone") or ""
    if user:
        first = first or (user.first_name or "")
        last = last or (user.last_name or "")
    fio = " ".join(x for x in [last, first, patr] if x)
    return {"ИМЯ": first, "ФАМИЛИЯ": last, "ОТЧЕСТВО": patr, "ФИО": fio, "ТЕЛЕФОН": phone}


def _admin_keyboard():
    # Запись клиентов ведётся в YCLIENTS — здесь только рассылка.
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="📢 Рассылка")]],
        resize_keyboard=True,
    )


def _client_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [_kbtn(PROFILE_BUTTON, EMOJI_PROFILE, style="primary")],
            [_kbtn(DOCTORS_BUTTON, EMOJI_DOCTORS)],
            [_kbtn(CONTACTS_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _share_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _skip_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=SKIP_BUTTON)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _confirm_keyboard():
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ Подтвердить", callback_data="broadcast:confirm"),
        types.InlineKeyboardButton(text="❌ Отмена",      callback_data="broadcast:cancel"),
    ]])


def _reminder_confirm_keyboard(booking_id):
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ ДА",  callback_data=f"remind:yes:{booking_id}"),
        types.InlineKeyboardButton(text="❌ НЕТ", callback_data=f"remind:no:{booking_id}"),
    ]])


# ── Тексты ────────────────────────────────────────────────────────────────────

CONFIRM_TEXT = (
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
)


def _reminder_text(booking: dict) -> str:
    """Текст напоминания о визите. Используется веб-панелью (кнопка «Напомнить»)."""
    name_parts = (booking.get("client_name") or "").strip().split()
    first = name_parts[0] if name_parts else "Уважаемый гость"
    t = booking["booking_time"].strftime("%H:%M")
    master = booking.get("master_name") or "нашему врачу-косметологу"
    return (
        f"{first}, добрый день! 🤍\n\n"
        f"Напоминаем, что завтра в {t} у Вас запланирован визит к нашему "
        f"врачу-косметологу — {master}🥰✨\n\n"
        f"Пожалуйста, подтвердите свою запись, нажав на кнопку ниже."
    )


# ── Утилита: красивое ФИО с заглавных букв ───────────────────────────────────

def _cap_name(s: str) -> str:
    """«иванова анна» → «Иванова Анна», с учётом дефисов (анна-мария → Анна-Мария)."""
    def cap_word(w):
        return "-".join((p[:1].upper() + p[1:].lower()) if p else p for p in w.split("-"))
    return " ".join(cap_word(w) for w in (s or "").strip().split())


# ── /start ─────────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    # /start всегда сбрасывает текущий шаг (в т.ч. посреди анкеты), чтобы не зависнуть.
    await state.clear()
    db.upsert_client(
        tg_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
    )

    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            f"👋 Добро пожаловать в панель *{CLINIC_NAME}*!\n\n"
            f"📢 *Рассылка* — отправить сообщение клиентам\n\n"
            f"_Запись клиентов ведётся в YClients._",
            parse_mode="Markdown",
            reply_markup=_admin_keyboard(),
        )
        return

    client = db.get_client_by_tg(message.from_user.id)

    if client and client.get("phone"):
        # Номер есть, но анкета не дозаполнена (нет даты рождения) — продолжаем анкету,
        # а не показываем «вы уже с нами» посреди регистрации.
        if not client.get("birth_date"):
            await state.set_state(RegForm.last_name)
            await message.answer(
                render_template("bot_reg_start"),
                parse_mode="HTML",
                reply_markup=types.ReplyKeyboardRemove(),
            )
            return
        # полностью зарегистрирован
        await message.answer(
            render_template("bot_after_phone", **_name_ctx(client=client, user=message.from_user)),
            parse_mode="HTML",
            reply_markup=_client_keyboard(),
        )
        return

    # старт в воронку считаем только для ещё не зарегистрированных (нет телефона)
    db.log_event("start", client_id=(client or {}).get("id"))
    await message.answer(
        render_template("bot_welcome", **_name_ctx(client=client, user=message.from_user)),
        parse_mode="HTML",
        reply_markup=_share_keyboard(),
    )


# ── Контакт + запуск анкеты ───────────────────────────────────────────────────

@dp.message(F.contact)
async def got_contact(message: types.Message, state: FSMContext):
    raw_phone = message.contact.phone_number
    phone = db.normalize_phone(raw_phone)
    tg_id = message.from_user.id
    user = message.from_user

    db.upsert_client(
        tg_id=tg_id,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
    )
    db.save_client_phone(tg_id, phone)
    client = db.get_client_by_tg(tg_id)
    if tg_id not in ADMIN_IDS:
        db.log_event("phone_confirmed", client_id=(client or {}).get("id"))

    await _notify_admins_new_contact(user, phone)

    if not client:
        await message.answer(
            "Пожалуйста, нажмите /start для регистрации.",
            reply_markup=_share_keyboard(),
        )
        return

    # Если анкета ещё не заполнена (нет даты рождения) — запускаем её.
    if tg_id not in ADMIN_IDS and not client.get("birth_date"):
        await state.set_state(RegForm.last_name)
        await message.answer(
            render_template("bot_reg_start"),
            parse_mode="HTML",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    # Уже зарегистрирован.
    after = render_template("bot_after_phone", **_name_ctx(client=client, user=user))
    db.save_message(client["id"], "out", after)
    await message.answer(after, parse_mode="HTML", reply_markup=_client_keyboard())


@dp.message(RegForm.last_name)
async def reg_last_name(message: types.Message, state: FSMContext):
    ln = _cap_name(message.text)
    if not ln:
        await message.answer("Пожалуйста, введите фамилию текстом:")
        return
    await state.update_data(reg_last=ln)
    await state.set_state(RegForm.first_name)
    await message.answer(render_template("bot_reg_firstname", **{"ФАМИЛИЯ": ln}), parse_mode="HTML")


@dp.message(RegForm.first_name)
async def reg_first_name(message: types.Message, state: FSMContext):
    fn = _cap_name(message.text)
    if not fn:
        await message.answer("Пожалуйста, введите имя текстом:")
        return
    await state.update_data(reg_first=fn)
    await state.set_state(RegForm.patronymic)
    await message.answer(
        render_template("bot_reg_patronymic"),
        parse_mode="HTML",
        reply_markup=_skip_keyboard(),
    )


@dp.message(RegForm.patronymic)
async def reg_patronymic(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    patr = "" if txt.lower() == SKIP_BUTTON.lower() else _cap_name(txt)
    await state.update_data(reg_patr=patr)
    await state.set_state(RegForm.birth)
    await message.answer(
        render_template("bot_reg_birth"),
        parse_mode="HTML",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@dp.message(RegForm.birth)
async def reg_birth(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    try:
        bd = datetime.strptime(txt, "%d.%m.%Y").date()
    except ValueError:
        await message.answer(
            "Не получилось распознать дату. Введите строго в формате ДД.ММ.ГГГГ, "
            "например `15.06.1995`:",
            parse_mode="Markdown",
        )
        return
    if bd > datetime.now().date() or bd.year < 1900:
        await message.answer("Похоже, дата некорректна. Проверьте и введите ещё раз (ДД.ММ.ГГГГ):")
        return

    data = await state.get_data()
    last = data.get("reg_last", "")
    first = data.get("reg_first", "")
    patr = data.get("reg_patr", "")
    await state.clear()

    db.execute(
        "UPDATE clients SET reg_last_name=%s, reg_first_name=%s, reg_patronymic=%s, "
        "birth_date=%s WHERE tg_id=%s",
        (last, first, patr, bd, message.from_user.id),
    )

    fio = " ".join(x for x in [last, first, patr] if x)
    await message.answer(
        render_template("bot_reg_done", **{"ФИО": fio, "ИМЯ": first, "ДАТА": bd.strftime("%d.%m.%Y")}),
        parse_mode="HTML",
        reply_markup=_client_keyboard(),
    )
    client = db.get_client_by_tg(message.from_user.id)
    after = render_template("bot_after_phone", **_name_ctx(client=client, user=message.from_user))
    if client:
        db.save_message(client["id"], "out", after)
    await message.answer(after, parse_mode="HTML", reply_markup=_client_keyboard())


async def _notify_admins_new_contact(user, phone: str):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📲 *Новый контакт*\n"
                f"👤 {user.first_name} {user.last_name or ''}\n"
                f"📱 {phone}",
                parse_mode="Markdown",
            )
        except Exception:
            pass


# ── /newuser — тестовый ввод телефона и ФИО вручную (только админ) ────────────

@dp.message(Command("newuser"))
async def cmd_newuser(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(TestUserForm.phone)
    await message.answer(
        "🧪 Тестовый режим.\n\n"
        "Введите номер телефона клиента в формате +79999999999:",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@dp.message(TestUserForm.phone)
async def newuser_phone(message: types.Message, state: FSMContext):
    phone = db.normalize_phone(message.text or "")
    if len(phone) != 11 or not phone.startswith("7"):
        await message.answer("Не похоже на номер. Введите в формате +79999999999:")
        return
    await state.update_data(nu_phone=phone)
    await state.set_state(TestUserForm.fio)
    await message.answer("Введите ФИО (Фамилия Имя Отчество):")


@dp.message(TestUserForm.fio)
async def newuser_fio(message: types.Message, state: FSMContext):
    parts = (message.text or "").strip().split()
    if not parts:
        await message.answer("Введите ФИО текстом:")
        return
    last = _cap_name(parts[0])
    first = _cap_name(parts[1]) if len(parts) >= 2 else ""
    patr = _cap_name(" ".join(parts[2:])) if len(parts) >= 3 else ""

    data = await state.get_data()
    phone = data.get("nu_phone", "")
    await state.clear()

    user = message.from_user
    db.upsert_client(
        tg_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
    )
    db.save_client_phone(user.id, phone)
    db.execute(
        "UPDATE clients SET reg_last_name=%s, reg_first_name=%s, reg_patronymic=%s WHERE tg_id=%s",
        (last, first, patr, user.id),
    )

    fio = " ".join(x for x in [last, first, patr] if x)
    await message.answer(
        f"✅ Готово (тест):\nТелефон: {phone}\nФИО: {fio}\n\nПоказываю профиль…",
        reply_markup=_client_keyboard(),
    )
    await _send_profile(message)


# ── Профиль клиента ──────────────────────────────────────────────────────────

def _days_with_us_phrase(first_visit):
    """«Вы с нами уже N дней» из даты первого визита (без сердечка)."""
    if not first_visit:
        return None
    n = (datetime.now().date() - first_visit).days
    if n < 0:
        n = 0
    nn, d = n % 100, n % 10
    if 11 <= nn <= 14:
        word = "дней"
    elif d == 1:
        word = "день"
    elif 2 <= d <= 4:
        word = "дня"
    else:
        word = "дней"
    return f"Вы с нами уже {n} {word}"


async def _download_avatar(user_id: int):
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count and photos.photos:
            best = photos.photos[0][-1]
            buf = BytesIO()
            await bot.download(best, destination=buf)
            return buf.getvalue()
    except Exception as e:
        logger.warning(f"avatar download failed for {user_id}: {e}")
    return None


_CAPTION_SKIP = "\x00"  # маркер пустой строки (строки с ним убираются)


def _profile_caption(greeting, name, nearest_text, last_visit_text=None, days_phrase=None) -> str:
    rendered = render_template("bot_profile_caption", **{
        "ПРИВЕТСТВИЕ":      greeting,
        "ИМЯ":              name,
        "ДНЕЙ_С_НАМИ":      days_phrase or _CAPTION_SKIP,
        "ПОСЛЕДНИЙ_ВИЗИТ":  last_visit_text or _CAPTION_SKIP,
        "БЛИЖАЙШАЯ_ЗАПИСЬ": nearest_text,
    })
    # строки с незаполненными значениями (нет стажа/последнего визита) убираем
    lines = [ln for ln in rendered.split("\n") if _CAPTION_SKIP not in ln]
    return "\n".join(lines).strip()


async def _build_profile_card(name: str, phone: str, avatar_tg_id: int = None):
    """Собирает карточку профиля (png + подпись) по имени/телефону клиента.
    Данные визитов/кэшбэка тянутся из YClients по телефону; аватар — по tg_id,
    если он задан. Переиспользуется и кнопкой «Мой профиль», и командой /user."""
    visits = 0
    nearest_text = "отсутствует"
    last_visit_text = None
    days_phrase = None
    bonus_text = None

    summary = await yclients.get_profile_summary(phone) if phone else None
    if summary:
        visits = summary.get("visits", 0)
        ndt = summary.get("nearest_dt")
        if ndt:
            # с временем приёма: «01.07.2026 в 14:15» (если время задано)
            if ndt.hour or ndt.minute:
                nearest_text = ndt.strftime("%d.%m.%Y в %H:%M")
            else:
                nearest_text = ndt.strftime("%d.%m.%Y")
        elif summary.get("nearest"):
            nearest_text = summary["nearest"].strftime("%d.%m.%Y")
        if summary.get("last_visit"):
            last_visit_text = summary["last_visit"].strftime("%d.%m.%Y")
        days_phrase = _days_with_us_phrase(summary.get("first_visit"))
        bonus = summary.get("bonus") or 0
        if bonus:
            bonus_text = str(int(round(bonus)))

    greeting = greeting_by_hour()
    avatar_bytes = None
    if avatar_tg_id and avatar_tg_id > 0:
        avatar_bytes = await _download_avatar(avatar_tg_id)

    png = render_profile_card(
        greeting=greeting,
        name=name,
        visits=visits,
        nearest_text=nearest_text,
        avatar_bytes=avatar_bytes,
        clinic_name=CLINIC_NAME,
        last_visit_text=last_visit_text,
        days_with_us=days_phrase,
        bonus_text=bonus_text,
    )
    caption = _profile_caption(greeting, name, nearest_text, last_visit_text, days_phrase)
    return png, caption


async def _send_profile(message: types.Message):
    user = message.from_user

    db.upsert_client(
        tg_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
    )
    client = db.get_client_by_tg(user.id)

    # Личный кабинет — только после подтверждения номера.
    if user.id not in ADMIN_IDS and not ((client or {}).get("phone") or "").strip():
        await message.answer(
            "Чтобы открыть личный кабинет, сначала подтвердите номер телефона — "
            "нажмите кнопку ниже 🙏",
            reply_markup=_share_keyboard(),
        )
        return

    if user.id not in ADMIN_IDS:
        db.log_event("profile", client_id=(client or {}).get("id"))

    phone = client.get("phone") if client else None
    reg_first = client.get("reg_first_name") if client else ""
    name = reg_first or user.first_name or "Гость"

    try:
        await message.bot.send_chat_action(message.chat.id, "upload_photo")
    except Exception:
        pass
    png, caption = await _build_profile_card(name, phone, user.id)
    photo = types.BufferedInputFile(png, filename="profile.png")
    keyboard = _admin_keyboard() if user.id in ADMIN_IDS else _client_keyboard()
    await message.answer_photo(
        photo, caption=caption, parse_mode="HTML", reply_markup=keyboard,
    )


@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    await _send_profile(message)


@dp.message(F.text == PROFILE_BUTTON)
async def cmd_profile_button(message: types.Message):
    await _send_profile(message)


# ── /user +79991234567 — карточка клиента по телефону (только админ) ───────────

@dp.message(Command("user"))
async def cmd_user(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        await message.answer("Использование: /user +79991234567")
        return

    phone = db.normalize_phone(arg)
    client = db.get_client_by_phone(phone)

    if client:
        name = client.get("reg_first_name") or client.get("first_name") or "Клиент"
        png, caption = await _build_profile_card(
            name, client.get("phone") or phone, client.get("tg_id"))
        photo = types.BufferedInputFile(png, filename="profile.png")
        await message.answer_photo(photo, caption=caption, parse_mode="HTML")
        return

    # В боте клиента нет — проверяем, есть ли он в YClients.
    summary = await yclients.get_profile_summary(phone)
    if summary:
        nm = summary.get("name") or "—"
        await message.answer(
            f"⚠️ Клиент «{nm}» найден в YClients, но не зарегистрирован в боте.")
    else:
        await message.answer("❌ Клиент не найден ни в боте, ни в YClients.")


# ── Наши врачи (карусель ◀ ▶) ─────────────────────────────────────────────────

def _doctors_list():
    """Врачи с фактически существующим фото, в нужном порядке (для карусели)."""
    out = []
    for d in db.get_all_doctors():
        photo = d.get("photo") or ""
        if photo and os.path.exists(os.path.join(DOCTORS_DIR, photo)):
            out.append(d)
    return out


def _doctor_caption(d):
    name = d.get("full_name", "")
    title = d.get("title") or ""
    cap = f"👩‍⚕️ <b>{name}</b>"
    if title:
        cap += f"\n{title}"
    return cap


def _doctor_nav_kb(idx, total):
    # Бесконечная карусель: стрелки заворачиваются по кругу.
    prev_i = (idx - 1) % total
    next_i = (idx + 1) % total
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        _ikbtn(_INVIS, f"doc:{prev_i}", EMOJI_ARROW_LEFT),
        _ikbtn(f"{idx + 1}/{total}", "doc:noop"),
        _ikbtn(_INVIS, f"doc:{next_i}", EMOJI_ARROW_RIGHT),
    ]])


@dp.message(F.text == DOCTORS_BUTTON)
async def cmd_doctors(message: types.Message):
    db.log_event("doctors")
    doctors = _doctors_list()
    if not doctors:
        await message.answer("Информация о врачах пока не добавлена.")
        return
    d = doctors[0]
    path = os.path.join(DOCTORS_DIR, d["photo"])
    try:
        await message.bot.send_chat_action(message.chat.id, "upload_photo")
    except Exception:
        pass
    await message.answer_photo(
        types.FSInputFile(path),
        caption=_doctor_caption(d),
        parse_mode="HTML",
        reply_markup=_doctor_nav_kb(0, len(doctors)),
    )


@dp.callback_query(F.data.startswith("doc:"))
async def cb_doctor_nav(callback: types.CallbackQuery):
    val = callback.data.split(":", 1)[1]
    if val == "noop":
        await callback.answer()
        return
    doctors = _doctors_list()
    if not doctors:
        await callback.answer()
        return
    idx = int(val) % len(doctors)
    d = doctors[idx]
    path = os.path.join(DOCTORS_DIR, d["photo"])
    try:
        await callback.message.edit_media(
            media=types.InputMediaPhoto(
                media=types.FSInputFile(path),
                caption=_doctor_caption(d),
                parse_mode="HTML",
            ),
            reply_markup=_doctor_nav_kb(idx, len(doctors)),
        )
    except Exception as e:
        logger.warning("Карусель врачей: %s", e)
    await callback.answer()


# ── Контакты ──────────────────────────────────────────────────────────────────

async def _answer_with_scheme(message: types.Message, text: str):
    """Отправляет текст вместе со схемой прохода (assets/scheme.jpg).
    Если подпись укладывается в лимит Telegram (1024) — текст идёт подписью к фото;
    иначе сначала текст, потом фото отдельным сообщением. Нет схемы — только текст."""
    has_scheme = os.path.exists(SCHEME_PATH)
    if has_scheme and len(text) <= 1024:
        try:
            await message.bot.send_chat_action(message.chat.id, "upload_photo")
        except Exception:
            pass
        try:
            await message.answer_photo(
                types.FSInputFile(SCHEME_PATH), caption=text, parse_mode="HTML")
            return
        except Exception as e:
            logger.warning("Схема прохода: не отправить подписью: %s", e)
    # запасной путь: текст отдельно (без превью ссылок), затем фото
    try:
        await message.answer(
            text, parse_mode="HTML",
            link_preview_options=types.LinkPreviewOptions(is_disabled=True))
    except Exception:
        await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
    if has_scheme:
        try:
            await message.answer_photo(types.FSInputFile(SCHEME_PATH))
        except Exception as e:
            logger.warning("Схема прохода: не отправить фото: %s", e)


@dp.message(F.text == CONTACTS_BUTTON)
async def cmd_contacts(message: types.Message):
    db.log_event("contacts")
    await _answer_with_scheme(message, render_template("bot_contacts"))


# ── Рассылка ───────────────────────────────────────────────────────────────────

@dp.message(F.text == "📢 Рассылка")
async def cmd_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    categories = db.get_all_categories()
    rows = [[types.InlineKeyboardButton(text="👥 Всем клиентам", callback_data="bc_cat:0")]]
    for c in categories:
        rows.append([types.InlineKeyboardButton(
            text=f"🏷 {c['name']}",
            callback_data=f"bc_cat:{c['id']}"
        )])
    await message.answer(
        "📢 Выберите *аудиторию* рассылки:",
        parse_mode="Markdown",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


@dp.callback_query(F.data.startswith("bc_cat:"))
async def cb_broadcast_cat(call: types.CallbackQuery, state: FSMContext):
    cat_id = int(call.data.split(":")[1])
    await call.message.edit_reply_markup(reply_markup=None)
    await state.update_data(bc_category_id=cat_id)
    await state.set_state(BroadcastForm.waiting_for_text)
    label = "всем клиентам" if cat_id == 0 else f"категории #{cat_id}"
    await call.message.answer(
        f"✏️ Введите текст рассылки ({label}):\n\n"
        f"_Поддерживается обычный текст._",
        parse_mode="Markdown",
    )
    await call.answer()


@dp.message(BroadcastForm.waiting_for_text)
async def process_broadcast_text(message: types.Message, state: FSMContext):
    await state.update_data(bc_text=message.text)
    await state.set_state(BroadcastForm.waiting_for_confirm)
    await message.answer(
        f"📋 *Превью сообщения:*\n\n{message.text}\n\n"
        f"Отправить рассылку?",
        parse_mode="Markdown",
        reply_markup=_confirm_keyboard(),
    )


@dp.callback_query(F.data.startswith("broadcast:"))
async def cb_broadcast_confirm(call: types.CallbackQuery, state: FSMContext):
    action = call.data.split(":")[1]
    await call.message.edit_reply_markup(reply_markup=None)

    if action == "cancel":
        await state.clear()
        await call.message.answer("❌ Рассылка отменена.", reply_markup=_admin_keyboard())
        await call.answer()
        return

    data = await state.get_data()
    text   = data.get("bc_text", "")
    cat_id = data.get("bc_category_id", 0)
    await state.clear()

    if cat_id == 0:
        recipients = db.get_all_client_ids()
    else:
        recipients = db.get_clients_by_category(cat_id)

    sent = 0
    failed = 0
    status_msg = await call.message.answer(f"⏳ Отправляю... 0/{len(recipients)}")

    for i, r in enumerate(recipients):
        try:
            await bot.send_message(r["tg_id"], text)
            sent += 1
        except TelegramRetryAfter as e:
            # Telegram попросил подождать (flood control) — ждём и пробуем ещё раз,
            # чтобы не терять сообщение, а не считать его ошибкой.
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.send_message(r["tg_id"], text)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1
        if (i + 1) % 10 == 0:
            try:
                await status_msg.edit_text(f"⏳ Отправляю... {i+1}/{len(recipients)}")
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"📨 Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}"
    )
    await call.answer()


# ── Ответы на напоминание ДА/НЕТ ──────────────────────────────────────────────

@dp.callback_query(F.data.startswith("remind:"))
async def cb_reminder_answer(call: types.CallbackQuery):
    parts = call.data.split(":")
    answer = parts[1]

    await call.message.edit_reply_markup(reply_markup=None)

    client = db.get_client_by_tg(call.from_user.id)
    confirm_text = None

    if answer == "yes":
        # Первичный или повторный приём — по данным YClients (есть ли прошлые визиты).
        phone = client.get("phone") if client else None
        is_repeat = False
        if phone:
            try:
                summary = await yclients.get_profile_summary(phone)
                if summary and (summary.get("visits") or summary.get("first_visit")):
                    is_repeat = True
            except Exception as e:
                logger.warning("YClients при подтверждении записи: %s", e)
        confirm_text = render_template(
            "bot_confirm_repeat" if is_repeat else "bot_confirm_first")
        await _answer_with_scheme(call.message, confirm_text)
        await call.answer("✅ Подтверждено!")
        status_text = "✅ ПОДТВЕРДИЛ запись"
    else:
        await call.answer("Понятно, оператор свяжется с вами.")
        status_text = "❌ НЕ подтвердил запись"

    who = call.from_user.first_name or "Клиент"
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"📋 *{who}* {status_text}", parse_mode="Markdown")
        except Exception:
            pass

    if client:
        db.save_message(client["id"], "in",
                        "✅ Клиент подтвердил запись" if answer == "yes"
                        else "❌ Клиент не подтвердил запись")
        if answer == "yes" and confirm_text:
            db.save_message(client["id"], "out", confirm_text)


# ── Входящие сообщения ────────────────────────────────────────────────────────

SYSTEM_BUTTONS = {"📢 Рассылка", PROFILE_BUTTON, DOCTORS_BUTTON, CONTACTS_BUTTON,
                  SKIP_BUTTON}


async def _push_new_message(name: str, preview: str, client_id: int):
    """Web Push админам о новом сообщении клиента. pywebpush синхронный — гоняем
    в отдельном потоке, чтобы не блокировать event loop бота."""
    try:
        await asyncio.to_thread(
            webpush.send_push_to_admins,
            f"💬 {name}", (preview or "Новое сообщение")[:140], f"/chats/{client_id}")
    except Exception as e:
        logger.warning("Web Push trigger: %s", e)


@dp.message(F.text)
async def incoming_message(message: types.Message, state: FSMContext):
    if message.text in SYSTEM_BUTTONS:
        return

    client = db.get_client_by_tg(message.from_user.id)
    if not client:
        user = message.from_user
        db.upsert_client(
            tg_id=user.id,
            username=user.username or "",
            first_name=user.first_name or "",
            last_name=user.last_name or "",
        )
        client = db.get_client_by_tg(user.id)
        if not client:
            await message.answer("❌ Ошибка регистрации. Попробуйте /start")
            return

    db.save_message(client["id"], "in", message.text)

    # Без parse_mode: спецсимволы (* _ [ ` и т.п.) в тексте клиента не должны
    # ломать отправку уведомления админу (иначе пуш молча терялся).
    name = db.client_display_name(client)
    note = f"💬 Сообщение от {name}\n📱 {client.get('phone') or '—'}\n\n{message.text}"
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, note)
        except Exception:
            pass
    await _push_new_message(name, message.text, client["id"])

    # Если номер ещё не подтверждён — мягко просим поделиться им (но сообщение
    # админу уже ушло). Для зарегистрированных — ничего лишнего.
    if message.from_user.id not in ADMIN_IDS and not (client.get("phone") or "").strip():
        await message.answer(
            "Спасибо за сообщение! 🙏 Чтобы мы могли с вами работать, пожалуйста, "
            "сначала подтвердите номер телефона — нажмите кнопку ниже.",
            reply_markup=_share_keyboard(),
        )


def _extract_media(message: types.Message):
    """Из входящего сообщения вытаскивает (media_type, file_id, filename).
    Тип — один из: photo / video / audio / document (для рендера в панели)."""
    if message.photo:
        return "photo", message.photo[-1].file_id, "photo.jpg"
    if message.video:
        return "video", message.video.file_id, (message.video.file_name or "video.mp4")
    if message.video_note:
        return "video_note", message.video_note.file_id, "video_note.mp4"
    if message.voice:
        return "audio", message.voice.file_id, "voice.ogg"
    if message.audio:
        return "audio", message.audio.file_id, (message.audio.file_name or "audio.mp3")
    if message.document:
        return "document", message.document.file_id, (message.document.file_name or "file")
    if message.sticker:
        return "photo", message.sticker.file_id, "sticker.webp"
    return None, None, None


@dp.message(F.photo | F.video | F.video_note | F.voice | F.audio | F.document | F.sticker)
async def incoming_media(message: types.Message, state: FSMContext):
    """Фото/видео/голосовые/файлы от клиента → сохраняем в чат панели + уведомляем админа.
    (Срабатывает только вне шагов регистрации — там приоритет у FSM-обработчиков.)"""
    client = db.get_client_by_tg(message.from_user.id)
    if not client:
        user = message.from_user
        db.upsert_client(
            tg_id=user.id, username=user.username or "",
            first_name=user.first_name or "", last_name=user.last_name or "",
        )
        client = db.get_client_by_tg(user.id)
        if not client:
            return

    media_type, file_id, fname = _extract_media(message)
    if not file_id:
        return

    caption = (message.caption or "").strip()
    # Подпись-метка (для превью в списке диалогов); в самом пузыре панель её прячет.
    auto_label = {"photo": "📷 Фото", "video": "🎬 Видео", "audio": "🎤 Голосовое",
                  "video_note": "⭕ Видеокружок"}.get(media_type, "📎 " + fname)
    text_to_save = caption or auto_label

    db.save_message(
        client["id"], "in", text_to_save,
        media_type=media_type, media_file_id=file_id, media_filename=fname,
    )

    # Уведомление админу (без parse_mode — спецсимволы не ломают отправку).
    note_label = {"photo": "📷 Фото", "video": "🎬 Видео", "audio": "🎤 Голосовое",
                  "video_note": "⭕ Видеокружок", "document": "📎 Файл"}.get(media_type, "Вложение")
    name = db.client_display_name(client)
    note = f"💬 Сообщение от {name}\n📱 {client.get('phone') or '—'}\n\n{note_label}"
    if caption:
        note += f": {caption}"
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, note)
        except Exception:
            pass
    await _push_new_message(name, note_label + (f": {caption}" if caption else ""), client["id"])

    if message.from_user.id not in ADMIN_IDS and not (client.get("phone") or "").strip():
        await message.answer(
            "Спасибо! 🙏 Чтобы мы могли с вами работать, пожалуйста, "
            "сначала подтвердите номер телефона — нажмите кнопку ниже.",
            reply_markup=_share_keyboard(),
        )


# ── Планировщик: напоминания о визите и поздравления с ДР ─────────────────────

def _client_first_name(client, fallback="Уважаемый гость") -> str:
    if not client:
        return fallback
    return client.get("reg_first_name") or client.get("first_name") or fallback


async def _send_due_reminders():
    """Раз в час: берём записи на завтра из YCLIENTS и шлём напоминания
    с кнопками «Подтвердить/Отменить». Каждая запись — один раз (дедуп по id)."""
    tomorrow = (clinic_now() + timedelta(days=1)).date()
    records = await yclients.get_appointments_for_date(tomorrow)
    if not records:
        return
    sent = 0
    for rec in records:
        rid = rec.get("record_id")
        phone = rec.get("phone")
        if not rid or not phone:
            continue
        if db.yc_reminder_sent(rid):
            continue
        client = db.get_client_by_phone(phone)
        # Нет Telegram-аккаунта — не помечаем отправленным: вдруг клиент
        # зарегистрируется до визита, тогда напомним в следующий час.
        if not client or not client.get("tg_id") or client["tg_id"] <= 0:
            continue
        # Имя для обращения — ТОЛЬКО из бота (регистрация/Telegram), не из YClients.
        name = _client_first_name(client)
        text = render_template("reminder", **{
            "ИМЯ":   name,
            "ФИО":   db.client_display_name(client),
            "ДАТА":  rec["datetime"].strftime("%d.%m.%Y"),
            "ВРЕМЯ": rec["datetime"].strftime("%H:%M"),
            "ВРАЧ":  rec.get("master") or "нашему врачу-косметологу",
        })
        try:
            await bot.send_message(client["tg_id"], text, parse_mode="HTML",
                                   reply_markup=_reminder_confirm_keyboard(rid))
            db.save_message(client["id"], "out", text)
            db.mark_yc_reminder_sent(rid)
            sent += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.warning("reminder send failed (record %s): %s", rid, e)
    if sent:
        logger.info("📨 Напоминаний отправлено: %s", sent)


async def _send_birthday_greetings():
    """Поздравления с ДР (если включено в вебе). Раз в год на клиента, не ночью."""
    if not db.get_setting("birthday_enabled"):
        return
    now = clinic_now()
    if now.hour < 9:   # не шлём раньше 9 утра по МСК
        return
    year = now.year
    sent = 0
    for c in db.get_birthday_clients_today():
        if db.birthday_already_sent(c["id"], year):
            continue
        text = render_template("birthday", **{
            "ИМЯ": _client_first_name(c, fallback="Дорогой клиент"),
            "ФИО": db.client_display_name(c),
        })
        try:
            await bot.send_message(c["tg_id"], text, parse_mode="HTML")
            db.save_message(c["id"], "out", text)
            db.mark_birthday_sent(c["id"], year)
            sent += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.warning("birthday send failed (client %s): %s", c["id"], e)
    if sent:
        logger.info("🎂 Поздравлений с ДР отправлено: %s", sent)


async def _send_booking_notifications():
    """Новые записи из YCLIENTS → клиенту приходит подтверждение «вы записаны»
    (ровно один раз). Очередь и лист ожидания пропускаем. Уведомляем только
    тех, кто есть и в YCLIENTS, и в нашем боте.

    При самом первом запуске уже существующие записи только помечаются как
    обработанные (без отправки), иначе подтверждения ушли бы по всем старым
    записям сразу."""
    records = await yclients.get_future_records(days=90)
    if records is None:        # ошибка/интеграция выключена — пробуем позже
        return
    seeded = db.get_setting("booking_notify_seeded")
    now = datetime.now()
    sent = 0
    for rec in records:
        rid = rec.get("record_id")
        if not rid or db.yc_booking_notified(rid):
            continue
        if not seeded:
            db.mark_yc_booking_notified(rid)   # первичная инициализация — без отправки
            continue

        master = (rec.get("master") or "").strip()
        low = master.lower()
        dt = rec.get("datetime")
        is_queue = (not master) or ("очеред" in low) or ("лист ожидан" in low)

        # Очередь / лист ожидания / прошедшее время — не уведомляем, помечаем.
        if is_queue or not dt or dt < now:
            db.mark_yc_booking_notified(rid)
            continue

        # Двухэтапную задержку убрали (по решению): уведомляем сразу при
        # обнаружении. Основной путь — мгновенный вебхук YClients
        # (/yclients/webhook в панели); этот опрос раз в 5 мин — лишь страховка
        # на случай пропущенного вебхука. Дедуп общий — yc_bookings_notified.
        phone = rec.get("phone")
        client = db.get_client_by_phone(phone) if phone else None
        if client and client.get("tg_id") and client["tg_id"] > 0:
            text = render_template("booking_created", **{
                "ИМЯ":   _client_first_name(client),
                "ДАТА":  dt.strftime("%d.%m.%Y"),
                "ВРЕМЯ": dt.strftime("%H:%M"),
                "ВРАЧ":  master,
            })
            try:
                await bot.send_message(client["tg_id"], text, parse_mode="HTML")
                db.save_message(client["id"], "out", text)
                sent += 1
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.warning("booking notify failed (record %s): %s", rid, e)
        db.mark_yc_booking_notified(rid)

    if not seeded:
        db.set_setting("booking_notify_seeded", True)
        logger.info("Уведомления о записи: первичная инициализация выполнена (без рассылки).")
    elif sent:
        logger.info("📨 Подтверждений записи отправлено: %s", sent)


async def _booking_loop():
    """Проверяет новые записи каждые 2 минуты (запасной путь; основной —
    мгновенный вебхук YClients). Чаще делать не стоит — лишняя нагрузка на API."""
    await asyncio.sleep(25)  # дать боту подняться
    while True:
        try:
            await _send_booking_notifications()
        except Exception as e:
            logger.warning("booking loop error: %s", e)
        await asyncio.sleep(120)


# Сколько дней храним медиа из чатов, потом авто-удаляем (чтобы диск не забивался).
MEDIA_RETENTION_DAYS = 180


def _cleanup_old_media():
    """Удаляет файлы из папки uploads старше MEDIA_RETENTION_DAYS. Безопасно: при
    ошибке просто пропускает. Текст старых сообщений остаётся, исчезает лишь медиа."""
    try:
        import admin_web  # поздний импорт: к моменту запуска оба модуля загружены
        folder = admin_web.UPLOAD_FOLDER
    except Exception:
        return 0
    if not os.path.isdir(folder):
        return 0
    cutoff = (datetime.now() - timedelta(days=MEDIA_RETENTION_DAYS)).timestamp()
    removed = 0
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except Exception:
            pass
    if removed:
        logger.info("Авто-чистка медиа: удалено %d старых файлов (>%d дн.)",
                    removed, MEDIA_RETENTION_DAYS)
    return removed


async def _scheduler_loop():
    """Фоновый цикл: раз в час проверяет напоминания, поздравления и чистит медиа."""
    await asyncio.sleep(15)  # дать боту подняться
    while True:
        try:
            await _send_due_reminders()
        except Exception as e:
            logger.warning("scheduler reminders error: %s", e)
        try:
            await _send_birthday_greetings()
        except Exception as e:
            logger.warning("scheduler birthday error: %s", e)
        try:
            _cleanup_old_media()
        except Exception as e:
            logger.warning("scheduler media cleanup error: %s", e)
        await asyncio.sleep(3600)


# ── Глобальный обработчик ошибок ────────────────────────────────────────────────
# Любое необработанное исключение в хендлере попадает сюда: пишем в лог и Sentry,
# вежливо отвечаем пользователю — и бот продолжает работать, а не падает.

@dp.errors()
async def on_unhandled_error(event: types.ErrorEvent):
    logger.exception("Необработанная ошибка бота: %s", event.exception)
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(event.exception)
    except Exception:
        pass
    try:
        upd = event.update
        if upd.message:
            await upd.message.answer("Упс, что-то пошло не так 🙏 Попробуйте ещё раз чуть позже.")
        elif upd.callback_query:
            await upd.callback_query.answer("Что-то пошло не так, попробуйте ещё раз.")
    except Exception:
        pass
    return True


# ── Точка входа ────────────────────────────────────────────────────────────────

def _migrate():
    """Добавляет поля анкеты в таблицу clients (idempotent, безопасно)."""
    db._safe_alter("ALTER TABLE clients ADD COLUMN IF NOT EXISTS reg_last_name  TEXT DEFAULT ''")
    db._safe_alter("ALTER TABLE clients ADD COLUMN IF NOT EXISTS reg_first_name TEXT DEFAULT ''")
    db._safe_alter("ALTER TABLE clients ADD COLUMN IF NOT EXISTS reg_patronymic TEXT DEFAULT ''")
    db._safe_alter("ALTER TABLE clients ADD COLUMN IF NOT EXISTS birth_date DATE")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    _migrate()
    logger.info("🤖 Бот запущен")
    asyncio.create_task(_scheduler_loop())
    asyncio.create_task(_booking_loop())
    # handle_signals=False: бот работает в главном потоке вместе с веб-сервером
    # в фоновом потоке. Если aiogram сам вешает обработчики сигналов, на Windows
    # боту прилетает ложное завершение через секунду после старта. Ctrl+C
    # по-прежнему обрабатывается в run.py.
    await dp.start_polling(bot, handle_signals=False)
