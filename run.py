"""
run.py — точка входа.
"""
import threading
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def start_web():
    from admin_web import run_web
    run_web()


if __name__ == "__main__":
    from database import init_db
    init_db()

    t = threading.Thread(target=start_web, daemon=True, name="web")
    t.start()
    logger.info("✅ Веб-панель: http://localhost:5000")
    logger.info("✅ Запускаю Telegram-бота...")

    from bot import main
    asyncio.run(main())
