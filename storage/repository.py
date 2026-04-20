"""
storage/repository.py — репозитории для основных таблиц.

GeneratedContentRepo вынесен в content_repo.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import desc, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from storage.models import GeneratedItem, Lesson, Poll, UsedQuestion

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  LessonRepo
# ═══════════════════════════════════════════════════════════════════════════

class LessonRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get_by_source(self, source_file: str) -> Lesson | None:
        r = await self.s.execute(select(Lesson).where(Lesson.source_file == source_file))
        return r.scalar_one_or_none()

    async def save(self, lesson: Lesson) -> Lesson:
        self.s.add(lesson)
        await self.s.flush()
        return lesson

    async def get_latest(self) -> Lesson | None:
        r = await self.s.execute(
            select(Lesson).order_by(desc(Lesson.ingested_at)).limit(1)
        )
        return r.scalar_one_or_none()

    async def get_fresh(self) -> Lesson | None:
        cutoff = datetime.utcnow() - timedelta(hours=settings.FRESH_LESSON_HOURS)
        r = await self.s.execute(
            select(Lesson)
            .where(Lesson.ingested_at >= cutoff)
            .order_by(desc(Lesson.ingested_at))
            .limit(1)
        )
        return r.scalar_one_or_none()

    async def get_all(self) -> list[Lesson]:
        r = await self.s.execute(select(Lesson).order_by(desc(Lesson.ingested_at)))
        return list(r.scalars().all())

    async def get_old_lessons(self, exclude_id: int | None = None) -> list[Lesson]:
        r = await self.s.execute(select(Lesson).order_by(desc(Lesson.ingested_at)))
        lessons = list(r.scalars().all())
        if exclude_id is not None:
            lessons = [l for l in lessons if l.id != exclude_id]
        return lessons

    async def count(self) -> int:
        r = await self.s.execute(select(func.count()).select_from(Lesson))
        return r.scalar_one()


# ═══════════════════════════════════════════════════════════════════════════
#  QuestionRepo
# ═══════════════════════════════════════════════════════════════════════════

class QuestionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get_recent(self, limit: int | None = None) -> list[str]:
        n = limit or settings.MAX_USED_QUESTIONS
        r = await self.s.execute(
            select(UsedQuestion.text_short)
            .order_by(desc(UsedQuestion.created_at))
            .limit(n)
        )
        return list(r.scalars().all())

    async def add_bulk(
        self,
        questions: list[str],
        item_id: int | None = None,
        lesson_id: int | None = None,
    ) -> None:
        for q in questions:
            self.s.add(UsedQuestion(
                text=q,
                text_short=q[:160],
                item_id=item_id,
                lesson_id=lesson_id,
            ))
        await self.s.flush()

    async def is_duplicate(self, question: str, threshold: int = 120) -> bool:
        short = question[:threshold]
        r = await self.s.execute(
            select(UsedQuestion.id).where(UsedQuestion.text_short == short).limit(1)
        )
        return r.scalar_one_or_none() is not None


# ═══════════════════════════════════════════════════════════════════════════
#  ItemRepo
# ═══════════════════════════════════════════════════════════════════════════

class ItemRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def save(self, item: GeneratedItem) -> GeneratedItem:
        self.s.add(item)
        await self.s.flush()
        return item

    async def mark_sent(self, item_id: int) -> None:
        r = await self.s.execute(
            select(GeneratedItem).where(GeneratedItem.id == item_id)
        )
        item = r.scalar_one_or_none()
        if item:
            item.sent = True
            item.sent_at = datetime.utcnow()
            await self.s.flush()

    async def get_latest_unsent(self) -> GeneratedItem | None:
        r = await self.s.execute(
            select(GeneratedItem)
            .where(GeneratedItem.sent == False)  # noqa: E712
            .order_by(desc(GeneratedItem.created_at))
            .limit(1)
        )
        return r.scalar_one_or_none()


# ═══════════════════════════════════════════════════════════════════════════
#  PollRepo
# ═══════════════════════════════════════════════════════════════════════════

class PollRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def save(self, poll: Poll) -> Poll:
        self.s.add(poll)
        await self.s.flush()
        return poll

    async def get_recent_questions(self, limit: int = 20) -> list[str]:
        r = await self.s.execute(
            select(Poll.question).order_by(desc(Poll.created_at)).limit(limit)
        )
        return list(r.scalars().all())
