"""
run_web.py — запуск ТОЛЬКО веб-панели (CRM).

Для сервера (systemd: reform-web.service). Telegram-бот поднимается отдельно
через run_bot.py — изоляция сбоев. Для локального «всё сразу» есть run.py.
"""
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    from monitoring import init_sentry
    if init_sentry("reform-web"):
        logger.info("Мониторинг Sentry активен (панель)")

    from database import init_db
    init_db()

    logger.info("✅ Веб-панель запускается...")
    from admin_web import run_web
    run_web()


if __name__ == "__main__":
    main()
