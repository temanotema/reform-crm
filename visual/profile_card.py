"""
visual/profile_card.py — генерация премиальной карточки профиля (Pillow).

Карточка — широкий баннер в стиле дорогой косметологической клиники:
тёплый ivory-фон, слева круглый аватар с мягкой тенью, справа приветствие,
строка «Вы с нами уже N дней ♥», число посещений, последний визит и
ближайшая запись.

Главная функция — render_profile_card(...) -> bytes (PNG).
Никаких обращений к БД или Telegram здесь нет.
"""

from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import theme as T

# Мягкий тёплый «сердечный» оттенок (не салонно-розовый, приглушённый).
HEART_COLOR = (188, 138, 128)


# ──────────────────────────────────────────────────────────────────────────────
#  СТАТУСЫ КЛИЕНТА (подготовлено, ПОКА НЕ ОТОБРАЖАЕТСЯ — SHOW_STATUS=False)
# ──────────────────────────────────────────────────────────────────────────────
SHOW_STATUS = False

STATUS_NEW     = "Новый клиент"
STATUS_REGULAR = "Постоянный клиент"
STATUS_VIP     = "VIP"

_STATUS_COLORS = {
    STATUS_NEW:     (T.BEIGE, T.DEEP_TAUPE),
    STATUS_REGULAR: (T.SOFT_SAND, T.WARM_WHITE),
    STATUS_VIP:     (T.DEEP_TAUPE, T.WARM_WHITE),
}


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _vertical_gradient(size, top_rgb, bottom_rgb):
    w, h = size
    top = Image.new("RGB", size, top_rgb)
    bottom = Image.new("RGB", size, bottom_rgb)
    mask = Image.new("L", (1, h))
    for y in range(h):
        mask.putpixel((0, y), int(255 * y / max(1, h - 1)))
    mask = mask.resize(size)
    return Image.composite(bottom, top, mask)


def _draw_tracked_text(draw, xy, text, font, fill, tracking=0):
    x, y = xy
    total = 0
    for ch in text:
        draw.text((x + total, y), ch, font=font, fill=fill, anchor="la")
        total += draw.textlength(ch, font=font) + tracking
    return max(0, total - tracking)


def _tracked_width(draw, text, font, tracking=0):
    total = 0
    for ch in text:
        total += draw.textlength(ch, font=font) + tracking
    return max(0, total - tracking)


def _truncate_to_width(draw, text, font, max_width):
    if draw.textlength(text, font=font) <= max_width:
        return text
    out = text
    while out and draw.textlength(out + "…", font=font) > max_width:
        out = out[:-1]
    return (out + "…") if out else "…"


def _circular_avatar(src_img, size):
    img = ImageOps.fit(src_img.convert("RGB"), (size, size), method=Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _default_avatar(size, initial):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size - 1, size - 1), fill=T.AVATAR_PLACEHOLDER_BG + (255,))
    letter = (initial or "·").upper()[:1]
    font = T.get_font("serif", int(size * 0.42))
    bbox = d.textbbox((0, 0), letter, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((size / 2 - tw / 2 - bbox[0], size / 2 - th / 2 - bbox[1]),
           letter, font=font, fill=T.WARM_WHITE)
    return img


def _paste_soft_shadow(canvas, center, radius, offset=(0, 14), blur=22):
    cx, cy = center
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    ox, oy = offset
    d.ellipse((cx - radius + ox, cy - radius + oy, cx + radius + ox, cy + radius + oy),
              fill=T.SHADOW_RGBA)
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def _draw_heart(canvas, x, y, size, rgb):
    """Рисует сердечко фигурой (не зависит от шрифта — глиф ♥ есть не везде).

    Рисуем в 4x и уменьшаем — для мягких краёв. (x, y) — левый верх рамки size×size.
    """
    ss = 4
    s = int(size * ss)
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fill = tuple(rgb) + (255,)
    # две верхние дольки
    d.ellipse((0, 0, s * 0.55, s * 0.58), fill=fill)
    d.ellipse((s * 0.45, 0, s, s * 0.58), fill=fill)
    # нижнее остриё
    d.polygon([(s * 0.02, s * 0.36), (s * 0.98, s * 0.36), (s * 0.5, s * 0.98)], fill=fill)
    layer = layer.resize((int(size), int(size)), Image.LANCZOS)
    canvas.alpha_composite(layer, (int(x), int(y)))


# ── Главная функция ───────────────────────────────────────────────────────────

def render_profile_card(
    greeting: str,
    name: str,
    visits: int,
    nearest_text: str,
    avatar_bytes: bytes = None,
    clinic_name: str = "Re.form Cosmetology",
    status: str = None,
    last_visit_text: str = None,
    days_with_us: str = None,
) -> bytes:
    """
    greeting        — «Доброе утро/день/вечер/Доброй ночи»
    name            — имя клиента (крупно)
    visits          — число посещений (int)
    nearest_text    — «12.07.2026» или «отсутствует»
    avatar_bytes    — байты фото из Telegram (или None → дефолтный аватар)
    clinic_name     — название клиники (бренд-строка сверху)
    status          — STATUS_* (показывается только при SHOW_STATUS=True)
    last_visit_text — дата последнего визита «14.05.2026» (или None)
    days_with_us    — фраза «Вы с нами уже 124 дня» БЕЗ сердечка (или None);
                      сердечко ♥ карточка дорисует сама.
    """
    W, H = T.CARD_WIDTH, T.CARD_HEIGHT

    canvas = _vertical_gradient((W, H), T.WARM_WHITE, T.IVORY).convert("RGBA")
    draw = ImageDraw.Draw(canvas)

    inset = 34
    draw.rectangle((inset, inset, W - inset, H - inset), outline=T.HAIRLINE, width=2)

    # ── Аватар ────────────────────────────────────────────────────────────────
    asize = T.AVATAR_SIZE
    cx = T.AVATAR_CENTER_X
    cy = H // 2
    radius = asize // 2

    _paste_soft_shadow(canvas, (cx, cy), radius)

    avatar_img = None
    if avatar_bytes:
        try:
            avatar_img = _circular_avatar(Image.open(BytesIO(avatar_bytes)), asize)
        except Exception:
            avatar_img = None
    if avatar_img is None:
        initial = name.strip()[0] if name and name.strip() else "·"
        avatar_img = _default_avatar(asize, initial)

    canvas.alpha_composite(avatar_img, (cx - radius, cy - radius))
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                 outline=T.BEIGE, width=T.AVATAR_RING)

    # ── Правая колонка ────────────────────────────────────────────────────────
    text_x = T.PADDING + asize + 80
    right_limit = W - T.PADDING
    avail = right_limit - text_x

    # Бренд
    brand_font = T.get_font("sans", T.SIZE_BRAND)
    _draw_tracked_text(draw, (text_x, 116), clinic_name.upper(), brand_font,
                       T.SOFT_SAND, tracking=T.TRACKING_BRAND)

    # Приветствие
    greet_font = T.get_font("serif", T.SIZE_GREETING)
    draw.text((text_x, 182), f"{greeting},", font=greet_font, fill=T.DEEP_TAUPE, anchor="la")

    # Имя
    name_font = T.get_font("serif_bold", T.SIZE_NAME)
    safe_name = name.strip() if name and name.strip() else "Гость"
    safe_name = _truncate_to_width(draw, safe_name, name_font, avail)
    name_y = 182 + T.SIZE_GREETING + 10
    draw.text((text_x, name_y), safe_name, font=name_font, fill=T.DEEP_TAUPE, anchor="la")

    # «Вы с нами уже N дней ♥»
    cursor_y = name_y + T.SIZE_NAME + 16
    if days_with_us:
        days_font = T.get_font("serif", 40)
        phrase = days_with_us.strip()
        w = _draw_tracked_text(draw, (text_x, cursor_y), phrase, days_font, T.SOFT_SAND, tracking=0)
        # сердечко рисуем фигурой (символ ♥ есть не во всех шрифтах → был квадрат)
        _draw_heart(canvas, text_x + w + 14, cursor_y + 8, 30, HEART_COLOR)
        cursor_y += 40 + 24
    else:
        cursor_y = name_y + T.SIZE_NAME + 40

    # Разделитель
    divider_y = cursor_y
    draw.line((text_x, divider_y, min(text_x + 580, right_limit), divider_y),
              fill=T.HAIRLINE, width=2)

    # ── Статистика (сетка 2 колонки) ──────────────────────────────────────────
    label_font = T.get_font("sans", T.SIZE_LABEL)
    value_font = T.get_font("serif", T.SIZE_VALUE)
    stats_y = divider_y + 26
    col1_x = text_x
    col2_x = text_x + 320
    row_gap = 96

    stats = [("ПОСЕЩЕНИЙ", str(visits))]
    if last_visit_text:
        stats.append(("ПОСЛЕДНИЙ ВИЗИТ", last_visit_text))
    stats.append(("БЛИЖАЙШАЯ ЗАПИСЬ", nearest_text))

    cells = [(col1_x, 0), (col2_x, 0), (col1_x, 1), (col2_x, 1)]
    for (label, value), (cell_x, row) in zip(stats, cells):
        y = stats_y + row * row_gap
        _draw_tracked_text(draw, (cell_x, y), label, label_font, T.SOFT_SAND, tracking=T.TRACKING_LABEL)
        draw.text((cell_x, y + 38), value, font=value_font, fill=T.DEEP_TAUPE, anchor="la")

    # ── status badge (выключено) ──────────────────────────────────────────────
    if SHOW_STATUS and status:
        bg_color, fg_color = _STATUS_COLORS.get(status, (T.BEIGE, T.DEEP_TAUPE))
        badge_font = T.get_font("sans", T.SIZE_BADGE)
        pad_x, pad_y = 26, 14
        tw = _tracked_width(draw, status.upper(), badge_font, tracking=T.TRACKING_LABEL)
        bw = tw + pad_x * 2
        bh = T.SIZE_BADGE + pad_y * 2
        bx, by = text_x, 116 - bh - 10
        draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=bh // 2, fill=bg_color)
        _draw_tracked_text(draw, (bx + pad_x, by + pad_y), status.upper(),
                           badge_font, fg_color, tracking=T.TRACKING_LABEL)

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


# ── Локальный тест без бота:  python -m visual.profile_card ───────────────────
if __name__ == "__main__":
    import os
    data = render_profile_card(
        greeting="Добрый день",
        name="Анна",
        visits=20,
        nearest_text="12.07.2026",
        avatar_bytes=None,
        clinic_name="Re.form Cosmetology",
        last_visit_text="14.05.2026",
        days_with_us="Вы с нами уже 124 дня",
    )
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sample_profile.png")
    with open(path, "wb") as f:
        f.write(data)
    print(f"✅ Образец карточки сохранён: {path}")
