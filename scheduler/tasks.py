"""
scheduler/tasks.py — планировщик постинга с приоритетом по дням урока.

Логика:
  - В дни урока (mon/thu) → режим latest
  - В остальные дни → mixed
  - Расписание читается из POST_TIMES и LESSON_DAYS
"""
from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from content.lesson_ingestor import LessonIngestor
from logic.lesson_service import LessonService

logger = logging.getLogger(__name__)

# Маппинг трёхбуквенных аббревиатур → номера дней недели (0=пн, 6=вс)
_DAY_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
}


def _is_lesson_day() -> bool:
    """True если сегодня день урока."""
    today = datetime.utcnow().weekday()
    lesson_days = [_DAY_MAP.get(d, -1) for d in settings.lesson_days_parsed]
    return today in lesson_days


async def _post_lesson(bot: Bot) -> None:
    """Задача планировщика: выбирает режим и отправляет урок."""
    mode = "latest" if _is_lesson_day() else "mixed"
    logger.info("📬 Плановый пост | mode=%s | is_lesson_day=%s", mode, _is_lesson_day())
    try:
        service = LessonService()
        await service.send_lesson(bot, settings.GROUP_CHAT_ID, mode=mode)
    except Exception as exc:
        logger.exception("❌ Ошибка планового поста: %s", exc)


async def _auto_ingest(bot: Bot) -> None:
    """
    В день урока — автоматически проверяем наличие новых PDF
    и обрабатываем их. Запускается вместе с постингом.
    """
    if not _is_lesson_day():
        return
    logger.info("🔍 Автоинжест: проверяю новые PDF...")
    try:
        ingestor = LessonIngestor()
        results = await ingestor.ingest_all_new()
        if results:
            for r in results:
                status = "✅" if r.success else "❌"
                logger.info("%s Ingested: %s (id=%s)", status, r.source_file, r.lesson_id)
        else:
            logger.info("Новых PDF не найдено")
    except Exception as exc:
        logger.exception("❌ Ошибка автоинжеста: %s", exc)


async def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """
    Создаёт AsyncIOScheduler.
    Для каждого времени из POST_TIMES создаёт два задания:
      1. Автоинжест (только в дни урока)
      2. Постинг урока
    """
    scheduler = AsyncIOScheduler(timezone="UTC")
    post_times = settings.post_times_parsed

    for i, (hour, minute) in enumerate(post_times):
        # Автоинжест — за 2 минуты до постинга
        pre_min = (minute - 2) % 60
        pre_hour = hour if minute >= 2 else (hour - 1) % 24

        scheduler.add_job(
            _auto_ingest,
            trigger=CronTrigger(hour=pre_hour, minute=pre_min),
            kwargs={"bot": bot},
            id=f"ingest_{i}",
            name=f"Автоинжест перед постом {i+1}",
            replace_existing=True,
            misfire_grace_time=300,
        )

        scheduler.add_job(
            _post_lesson,
            trigger=CronTrigger(hour=hour, minute=minute),
            kwargs={"bot": bot},
            id=f"post_{i}",
            name=f"Пост {i+1} ({hour:02d}:{minute:02d} UTC)",
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("Запланирован пост %d: %02d:%02d UTC", i + 1, hour, minute)

    lesson_days_str = ", ".join(settings.lesson_days_parsed)
    logger.info("Дни урока: %s | Режим в эти дни: latest", lesson_days_str)
    return scheduler
