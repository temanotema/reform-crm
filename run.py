"""
run.py — точка входа.
"""
import threading
import asyncio
import logging
import signal
import sys
import time
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def start_web():
    from admin_web import run_web
    run_web()


# ── Диагностика/защита от «левого» SIGINT ──────────────────────────────────────
# Бот стабильно умирал ~через 1.4 c после старта от KeyboardInterrupt (Ctrl+C),
# который приходит ИЗВНЕ процесса (лаунчер/консоль/IDE), а не из-за сбоя опроса.
# Обработчик ниже: логирует КАЖДЫЙ SIGINT со стеком (видно источник) и игнорирует
# первые срабатывания, чтобы случайный Ctrl+C не ронял бота. Чтобы реально
# остановить — нажми Ctrl+C три раза за 2 секунды (или закрой окно / Ctrl+Break).
_sigint_times: list[float] = []


def _on_sigint(signum, frame):
    now = time.monotonic()
    _sigint_times.append(now)
    recent = [t for t in _sigint_times if now - t <= 2.0]
    logger.warning(
        "⚠️ Получен SIGINT (#%d за 2 c). Источник (стек на момент сигнала):\n%s",
        len(recent),
        "".join(traceback.format_stack(frame)),
    )
    if len(recent) >= 3:
        logger.info("Три Ctrl+C подряд — выходим.")
        raise KeyboardInterrupt
    logger.info("Игнорирую этот SIGINT, бот продолжает работу.")


if __name__ == "__main__":
    from monitoring import init_sentry
    if init_sentry("reform"):
        logger.info("Мониторинг Sentry активен")

    from database import init_db
    init_db()

    try:
        signal.signal(signal.SIGINT, _on_sigint)
        if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _on_sigint)
    except (ValueError, OSError) as e:
        logger.warning("Не удалось установить обработчик сигналов: %s", e)

    t = threading.Thread(target=start_web, daemon=True, name="web")
    t.start()
    logger.info("✅ Веб-панель: http://localhost:5000")
    logger.info("✅ Запускаю Telegram-бота...")

    from bot import main
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено.")
