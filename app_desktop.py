"""
app_desktop.py — точка входа десктоп-приложения Re.form CRM.

Открывает нативное окно (pywebview) с веб-панелью, поднимает Flask и
Telegram-бота в фоне. База и загрузки хранятся в папке пользователя
(%LOCALAPPDATA%\\ReformCRM), чтобы установленное приложение могло писать данные.

Запуск из исходников:  python app_desktop.py
Сборка в .exe — см. reform.spec и .github/workflows/build.yml.
"""

import os
import sys
import io
import time
import socket
import asyncio
import logging
import threading


# В упакованном Windows-приложении консоль может быть в cp1251 или вовсе
# отсутствовать — тогда print('✅' ...) валит запуск. Делаем вывод безопасным.
def _harden_stdio():
    for name in ("stdout", "stderr"):
        st = getattr(sys, name, None)
        if st is None:
            setattr(sys, name, io.StringIO())
        else:
            try:
                st.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

_harden_stdio()

# ── Папка данных (база + загрузки) — до импорта database/admin_web! ───────────
_BASE = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
DATA_DIR = os.path.join(_BASE, "ReformCRM")
os.makedirs(DATA_DIR, exist_ok=True)
os.environ.setdefault("REFORM_DATA_DIR", DATA_DIR)
os.environ.setdefault("DB_PATH", os.path.join(DATA_DIR, "cosmo.db"))

import webview  # pywebview

from config import WEB_PORT, CLINIC_NAME
from version import __version__

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("desktop")


def _start_web():
    from admin_web import run_web
    run_web()


def _start_bot():
    try:
        from bot import main as bot_main
        asyncio.run(bot_main())
    except Exception as e:
        logger.warning("Telegram-бот не запущен (проверь токен в config): %s", e)


def _wait_web(port, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def main():
    # База готовится один раз до старта потоков.
    from database import init_db
    init_db()

    threading.Thread(target=_start_web, daemon=True, name="web").start()
    threading.Thread(target=_start_bot, daemon=True, name="bot").start()

    _wait_web(WEB_PORT)

    webview.create_window(
        f"{CLINIC_NAME} — CRM  ·  v{__version__}",
        f"http://127.0.0.1:{WEB_PORT}/",
        width=1280, height=820, min_size=(960, 640),
    )
    webview.start()  # блокирует главный поток до закрытия окна


if __name__ == "__main__":
    main()
    os._exit(0)
