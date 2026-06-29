"""
run_bot.py — запуск ТОЛЬКО Telegram-бота.

Для сервера (systemd: reform-bot.service). Веб-панель поднимается отдельно
через run_web.py — так сбой одного сервиса не роняет другой.
Для локального «всё сразу» по-прежнему есть run.py.
"""
import asyncio
import logging
import signal
import sys
import time
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── Защита от «левого» SIGINT (см. историю в run.py) ──────────────────────────
# Стабильно ловили KeyboardInterrupt извне процесса (консоль/IDE/лаунчер),
# который ронял бота. Игнорируем одиночные Ctrl+C; для остановки — три за 2 c
# (под systemd прилетает SIGTERM, эта защита там не мешает).
_sigint_times: list[float] = []


def _on_sigint(signum, frame):
    now = time.monotonic()
    _sigint_times.append(now)
    recent = [t for t in _sigint_times if now - t <= 2.0]
    logger.warning("⚠️ SIGINT (#%d за 2 c).", len(recent))
    if len(recent) >= 3:
        logger.info("Три Ctrl+C подряд — выходим.")
        raise KeyboardInterrupt
    logger.info("Игнорирую этот SIGINT, бот продолжает работу.")


def main():
    from monitoring import init_sentry
    if init_sentry("reform-bot"):
        logger.info("Мониторинг Sentry активен (бот)")

    from database import init_db
    init_db()

    try:
        signal.signal(signal.SIGINT, _on_sigint)
        if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _on_sigint)
    except (ValueError, OSError) as e:
        logger.warning("Не удалось установить обработчик сигналов: %s", e)

    logger.info("✅ Запускаю Telegram-бота...")
    from bot import main as bot_main
    try:
        asyncio.run(bot_main())
    except KeyboardInterrupt:
        logger.info("Остановлено.")


if __name__ == "__main__":
    main()
