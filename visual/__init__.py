"""
visual/ — визуальный слой Telegram-бота Re.form Cosmetology.

Здесь собрана вся «косметика»: палитра и шрифты (theme.py),
централизованные эмодзи (emoji_manager.py), премиальные тексты-хелперы
(texts.py) и генерация карточки профиля на Pillow (profile_card.py).

Бизнес-логика бота сюда НЕ выносится. Этот пакет ничего не знает о БД —
он только готовит текст и картинки.
"""

from . import theme
from . import emoji_manager
from . import texts
from . import profile_card

__all__ = ["theme", "emoji_manager", "texts", "profile_card"]
