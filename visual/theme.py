"""
visual/theme.py — единый источник визуального стиля.

Здесь живут палитра, размеры карточки профиля и загрузчик шрифтов.
Менять оформление (цвета, шрифты, размеры) нужно ТОЛЬКО здесь —
тогда весь визуал бота меняется в одном месте.

Стиль: Aesop / premium aesthetic clinic / calm minimalism.
"""

import os
from PIL import ImageFont

# ── Базовые пути ──────────────────────────────────────────────────────────────
# Папка visual/ (та, где лежит этот файл).
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
FONTS_DIR  = os.path.join(ASSETS_DIR, "fonts")
DEFAULTS_DIR = os.path.join(ASSETS_DIR, "defaults")


# ── Палитра ───────────────────────────────────────────────────────────────────
# Тёплая ivory-гамма. Значения — RGB-кортежи.
# Hex для справки:
#   IVORY        #F5F1EB
#   WARM_WHITE   #FFFCF8
#   BEIGE        #D8C5B1
#   SOFT_SAND    #8D847A
#   DEEP_TAUPE   #3A342E
# Фирменный бордово-кремовый стиль клиники.
IVORY       = (82, 15, 24)      # фон карточки (низ градиента) — глубокий бордовый
WARM_WHITE  = (96, 20, 30)      # фон карточки (верх градиента) — чуть светлее
BEIGE       = (194, 171, 148)   # тёплый таупе (кольцо аватара)
SOFT_SAND   = (193, 161, 137)   # приглушённый кремово-розовый (подписи)
DEEP_TAUPE  = (243, 233, 218)   # основной кремовый текст

# Производные оттенки
HAIRLINE    = (120, 54, 64)     # тонкие линии-разделители (светлее фона)
SHADOW_RGBA = (0, 0, 0, 80)     # цвет мягкой тени (с альфой)
AVATAR_PLACEHOLDER_BG = (194, 171, 148)  # фон дефолтного аватара (таупе)


# ── Размеры карточки профиля ──────────────────────────────────────────────────
# Широкий баннер (пропорции близкие к YouTube channel art / 16:9).
CARD_WIDTH   = 1280
CARD_HEIGHT  = 720
# Внутренние поля
PADDING      = 90

# Аватар
AVATAR_SIZE  = 360      # диаметр круга аватара (px)
AVATAR_RING  = 6        # толщина кольца вокруг аватара
AVATAR_CENTER_X = PADDING + AVATAR_SIZE // 2   # центр круга по X

# Типографика (размеры в px). Подобраны под ширину 1280.
SIZE_GREETING = 70      # «Добрый день,»
SIZE_NAME     = 84      # имя
SIZE_LABEL    = 30      # подписи (ПОСЕЩЕНИЙ / БЛИЖАЙШАЯ ЗАПИСЬ)
SIZE_VALUE    = 46      # значения
SIZE_BRAND    = 30      # название клиники
SIZE_BADGE    = 30      # текст статуса (если включат)

# Межбуквенный интервал для подписей/бренда (имитация tracking)
TRACKING_LABEL = 4
TRACKING_BRAND = 6


# ── Шрифты ────────────────────────────────────────────────────────────────────
# Логика: сначала пытаемся взять кастомные .ttf из visual/assets/fonts/,
# затем системные шрифты Windows (у тебя проект на Windows), затем шрифты,
# которые поставляются с Pillow, и в самом конце — встроенный bitmap-шрифт.
#
# Чтобы поставить «премиальную» типографику — положи в visual/assets/fonts/:
#   - serif:      EBGaramond-Regular.ttf  (или Cormorant-Regular.ttf)
#   - sans:       Inter-Regular.ttf       (или Montserrat-Regular.ttf)
#   - sans_bold:  Inter-SemiBold.ttf      (или Montserrat-SemiBold.ttf)
# и пропиши их имена в _FONT_CANDIDATES ниже (первыми они уже учтены).

_FONT_CANDIDATES = {
    "script": [
        os.path.join(os.path.dirname(BASE_DIR), "duende.ttf"),   # корень проекта
        os.path.join(FONTS_DIR, "duende.ttf"),
        os.path.join(FONTS_DIR, "GreatVibes-Regular.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ],
    "serif": [
        os.path.join(FONTS_DIR, "EBGaramond-Regular.ttf"),
        os.path.join(FONTS_DIR, "Cormorant-Regular.ttf"),
        "C:/Windows/Fonts/georgia.ttf",
        "C:/Windows/Fonts/times.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "DejaVuSerif.ttf",
    ],
    "serif_bold": [
        os.path.join(FONTS_DIR, "EBGaramond-SemiBold.ttf"),
        os.path.join(FONTS_DIR, "Cormorant-SemiBold.ttf"),
        "C:/Windows/Fonts/georgiab.ttf",
        "C:/Windows/Fonts/timesbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "DejaVuSerif-Bold.ttf",
    ],
    "sans": [
        os.path.join(FONTS_DIR, "Inter-Regular.ttf"),
        os.path.join(FONTS_DIR, "Montserrat-Regular.ttf"),
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    ],
    "sans_bold": [
        os.path.join(FONTS_DIR, "Inter-SemiBold.ttf"),
        os.path.join(FONTS_DIR, "Montserrat-SemiBold.ttf"),
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
    ],
}

# Небольшой кэш, чтобы не открывать файл шрифта повторно.
_font_cache = {}


def get_font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    """
    Возвращает шрифт нужного типа и размера.

    kind: "serif" | "serif_bold" | "sans" | "sans_bold"
    size: размер в пикселях.

    Никогда не падает: если ни один .ttf не найден, вернёт встроенный
    шрифт Pillow (load_default) — карточка всё равно отрисуется.
    """
    cache_key = (kind, size)
    if cache_key in _font_cache:
        return _font_cache[cache_key]

    candidates = _FONT_CANDIDATES.get(kind, _FONT_CANDIDATES["sans"])
    font = None
    for path in candidates:
        try:
            font = ImageFont.truetype(path, size)
            break
        except (OSError, IOError):
            continue

    if font is None:
        # Самый последний fallback — встроенный bitmap-шрифт.
        try:
            font = ImageFont.load_default(size=size)  # Pillow >= 10
        except TypeError:
            font = ImageFont.load_default()           # старые версии Pillow

    _font_cache[cache_key] = font
    return font


def font_available(kind: str) -> bool:
    """True, если для типа найден реальный .ttf (а не bitmap-fallback)."""
    for path in _FONT_CANDIDATES.get(kind, []):
        if os.path.exists(path):
            return True
    return False
