"""
visual/profile_card.py — генерация премиальной карточки профиля (Pillow).

Карточка — широкий баннер в стиле дорогой косметологической клиники:
тёплый ivory-фон, слева круглый аватар с мягкой тенью, справа приветствие,
строка «Вы с нами уже N дней ♥», число посещений, последний визит и
ближайшая запись.

Главная функция — render_profile_card(...) -> bytes (PNG).
Никаких обращений к БД или Telegram здесь нет.
"""

import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import theme as T

# Тёплый розово-кремовый акцент (сердечко и звезда) на бордовом фоне.
HEART_COLOR = (216, 164, 140)
# Цвет звёздочки-«искры» рядом с бонусами (кэшбэк из YClients).
STAR_COLOR = (216, 164, 140)
# Золотые акценты (рамки, кольцо аватара, разделители, вотермарк).
GOLD      = (188, 150, 92)
GOLD_SOFT = (150, 118, 74)


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
           letter, font=font, fill=(255, 250, 244))
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


def _draw_star(canvas, x, y, size, rgb):
    """Четырёхлучевая звёздочка-«искра» (как ✦). (x, y) — левый верх рамки size×size."""
    ss = 4
    s = int(size * ss)
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx = cy = s / 2
    R = s / 2
    r = R * 0.34
    pts = []
    for i in range(8):
        ang = -math.pi / 2 + i * math.pi / 4
        rad = R if i % 2 == 0 else r
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    d.polygon(pts, fill=tuple(rgb) + (255,))
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
    bonus_text: str = None,
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

    # Двойная золотая рамка-«волосяная линия»
    inset = 34
    draw.rectangle((inset, inset, W - inset, H - inset), outline=GOLD, width=2)
    draw.rectangle((inset + 8, inset + 8, W - inset - 8, H - inset - 8),
                   outline=GOLD_SOFT, width=1)

    # Едва заметный вотермарк-логотип «Re.form» в правом нижнем углу
    try:
        wm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        wd = ImageDraw.Draw(wm)
        wm_font = T.get_font("script", 300)
        wd.text((W - 60, H - 40), "Re.form", font=wm_font, fill=GOLD + (255,), anchor="rs")
        a = wm.split()[3].point(lambda v: int(v * 0.06))
        wm.putalpha(a)
        canvas.alpha_composite(wm)
        draw = ImageDraw.Draw(canvas)
    except Exception:
        pass

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
    # Кольцо аватара: тонкое золотое + едва заметный внешний волосок
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                 outline=GOLD, width=3)
    draw.ellipse((cx - radius - 7, cy - radius - 7, cx + radius + 7, cy + radius + 7),
                 outline=GOLD_SOFT, width=1)

    # ── Правая колонка ────────────────────────────────────────────────────────
    text_x = T.PADDING + asize + 80
    right_limit = W - T.PADDING
    avail = right_limit - text_x

    # Бренд — логотип прописью (duende.ttf) + подзаголовок
    logo_y = 44
    LOGO_SIZE = 120          # ← размер логотипа «Re.form» (меняй это число)
    logo_font = T.get_font("script", LOGO_SIZE)
    draw.text((text_x, logo_y), "Re. form", font=logo_font, fill=T.DEEP_TAUPE, anchor="la")
    lbbox = draw.textbbox((text_x, logo_y), "Re. form", font=logo_font)
    tag_y = lbbox[3] + 2
    tag_font = T.get_font("sans", 21)
    _draw_tracked_text(draw, (text_x, tag_y), "КЛИНИКА ЭСТЕТИЧЕСКОЙ МЕДИЦИНЫ",
                       tag_font, T.SOFT_SAND, tracking=6)

    # Приветствие
    greet_y = tag_y + 21 + 20
    greet_font = T.get_font("serif", T.SIZE_GREETING)
    draw.text((text_x, greet_y), f"{greeting},", font=greet_font, fill=T.DEEP_TAUPE, anchor="la")

    # Имя (облегчённое начертание — тоньше, изящнее)
    name_font = T.get_font("serif", T.SIZE_NAME)
    safe_name = name.strip() if name and name.strip() else "Гость"
    safe_name = _truncate_to_width(draw, safe_name, name_font, avail)
    name_y = greet_y + T.SIZE_GREETING + 10
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
              fill=GOLD_SOFT, width=1)

    # ── Статистика ────────────────────────────────────────────────────────────
    # Верхний ряд: до трёх показателей (посещения / последний визит / бонусы)
    # с тонкими вертикальными разделителями. Размер шрифта подбирается так,
    # чтобы три колонки гарантированно влезли по ширине при любом шрифте.
    stats_y = divider_y + 26

    items = [("ПОСЕЩЕНИЙ", str(visits), False)]
    if last_visit_text:
        items.append(("ПОСЛЕДНИЙ ВИЗИТ", last_visit_text, False))
    if bonus_text:
        items.append(("БОНУСЫ", str(bonus_text), True))

    min_gap = 28
    lbl_sz, val_sz, widths = 24, 42, []
    for ls, vs in [(24, 42), (22, 40), (20, 38), (18, 35), (16, 32)]:
        lf = T.get_font("sans", ls)
        vf = T.get_font("serif", vs)
        widths = []
        for (label, value, star) in items:
            lw = _tracked_width(draw, label, lf, T.TRACKING_LABEL)
            vw = draw.textlength(value, font=vf) + (vs if star else 0)
            widths.append(max(lw, vw))
        lbl_sz, val_sz = ls, vs
        if sum(widths) + min_gap * (len(items) - 1) <= avail:
            break

    label_font = T.get_font("sans", lbl_sz)
    value_font = T.get_font("serif", val_sz)
    val_y = stats_y + lbl_sz + 14
    val_bottom = val_y + val_sz
    gaps = max(1, len(items) - 1)
    extra = max(0, avail - (sum(widths) + min_gap * (len(items) - 1)))
    gap = min_gap + (extra / gaps if len(items) > 1 else 0)

    x = float(text_x)
    for i, (label, value, star) in enumerate(items):
        cell_x = int(round(x))
        if i > 0:
            dx = cell_x - int(gap / 2)
            draw.line((dx, stats_y - 2, dx, val_bottom), fill=GOLD_SOFT, width=1)
        _draw_tracked_text(draw, (cell_x, stats_y), label, label_font, T.SOFT_SAND,
                           tracking=T.TRACKING_LABEL)
        draw.text((cell_x, val_y), value, font=value_font, fill=T.DEEP_TAUPE, anchor="la")
        if star:
            vw = draw.textlength(value, font=value_font)
            _draw_star(canvas, cell_x + vw + 12, val_y + int(val_sz * 0.16), int(val_sz * 0.72), STAR_COLOR)
        x += widths[i] + gap

    # Ближайшая запись — отдельной строкой под верхним рядом.
    near_y = val_bottom + 34
    near_label_font = T.get_font("sans", 26)
    near_value_font = T.get_font("serif", 44)
    _draw_tracked_text(draw, (text_x, near_y), "БЛИЖАЙШАЯ ЗАПИСЬ", near_label_font, T.SOFT_SAND,
                       tracking=T.TRACKING_LABEL)
    draw.text((text_x, near_y + 36), nearest_text, font=near_value_font, fill=T.DEEP_TAUPE, anchor="la")

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
        bonus_text="156",
    )
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sample_profile.png")
    with open(path, "wb") as f:
        f.write(data)
    print(f"Карточка сохранена: {path}")
