"""
main.py — точка входа Python Teacher Bot v2.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.handlers import router
from config import settings
from scheduler.tasks import setup_scheduler
from storage.db import init_db

# ── Логирование ────────────────────────────────────────────────────────────
Path("data").mkdir(parents=True, exist_ok=True)
Path("materials").mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("🚀 Запуск Python Teacher Bot v2...")

    # Инициализация БД
    await init_db()

    # Автоинжест при старте
    try:
        from content.lesson_ingestor import LessonIngestor
        ingestor = LessonIngestor()
        new_pdfs = await ingestor.find_new_pdfs()
        if new_pdfs:
            logger.info("Найдено %d новых PDF при старте, обрабатываю...", len(new_pdfs))
            results = await ingestor.ingest_all_new()
            for r in results:
                logger.info("  %s → %s (id=%s)", "✅" if r.success else "❌",
                            r.source_file, r.lesson_id)
    except Exception as exc:
        logger.warning("Автоинжест при старте: %s", exc)

    # Telegram Bot + Dispatcher
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    # Планировщик
    scheduler = await setup_scheduler(bot)
    scheduler.start()
    logger.info("✅ Планировщик запущен (%d заданий)", len(scheduler.get_jobs()))

    try:
        logger.info("✅ Бот запущен. Ожидаю сообщения...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        from llm.ollama_client import close_session
        await close_session()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
