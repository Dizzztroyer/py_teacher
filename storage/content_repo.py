"""
storage/content_repo.py — репозиторий для таблицы generated_content.

Отвечает за:
  - сохранение новых записей
  - проверку дублей по SHA-256 hash
  - выборку истории для аналитики
  - пометку записей как "записано в MD"
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from storage.hashing import content_hash, question_hash
from storage.models import GeneratedContent

logger = logging.getLogger(__name__)


class GeneratedContentRepo:
    """
    Репозиторий generated_content.

    Ключевые принципы:
      - Дедупликация ТОЛЬКО через БД (hash-индекс), не через файл.
      - Файл history.md — только для чтения людьми, не для проверок.
      - UniqueConstraint на hash гарантирует консистентность даже при
        конкурентных вставках.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    # ── Основные операции ──────────────────────────────────────────────────

    async def save(
        self,
        *,
        question: str,
        poll_question: str,
        options: list[str],
        correct_option: int,
        explanation: str,
        difficulty: str,
        lesson_id: int | None,
        source_name: str,
    ) -> tuple[GeneratedContent, bool]:
        """
        Сохраняет запись в generated_content.

        Returns:
            (record, is_new): is_new=False если запись уже существовала (дубль).

        Логика дедупликации:
          1. Вычисляем hash = SHA-256(normalize(question))
          2. SELECT по hash — быстро (индекс)
          3. Если существует → возвращаем existing + is_new=False
          4. Если нет → INSERT; при конкурентном конфликте UniqueConstraint
             возвращает existing + is_new=False
        """
        h = question_hash(question)

        # ── Проверка существующего hash ──────────────────────────────────
        existing = await self._get_by_hash(h)
        if existing is not None:
            logger.info(
                "🔁 Дубль обнаружен: hash=%s… | вопрос=%r",
                h[:12], question[:60],
            )
            return existing, False

        # ── Новая запись ──────────────────────────────────────────────────
        record = GeneratedContent(
            lesson_id=lesson_id,
            source_name=source_name,
            question=question,
            poll_question=poll_question,
            options_json=json.dumps(options, ensure_ascii=False),
            correct_option=correct_option,
            explanation=explanation,
            difficulty=difficulty,
            hash=h,
            written_to_md=False,
        )

        try:
            self.s.add(record)
            await self.s.flush()
            logger.info(
                "✅ GeneratedContent сохранён: id=%d hash=%s…",
                record.id, h[:12],
            )
            return record, True

        except IntegrityError:
            # Конкурентная вставка с тем же hash (маловероятно, но возможно)
            await self.s.rollback()
            existing = await self._get_by_hash(h)
            logger.warning("IntegrityError на hash %s — возвращаю существующий", h[:12])
            return existing, False  # type: ignore[return-value]

    async def mark_written_to_md(self, record_id: int) -> None:
        """Помечает запись как записанную в history.md."""
        result = await self.s.execute(
            select(GeneratedContent).where(GeneratedContent.id == record_id)
        )
        record = result.scalar_one_or_none()
        if record:
            record.written_to_md = True
            await self.s.flush()

    # ── Поиск и проверки ──────────────────────────────────────────────────

    async def is_duplicate(self, question: str) -> bool:
        """
        Быстрая проверка: существует ли уже вопрос с таким hash?

        Используется ПЕРЕД генерацией через LLM чтобы не тратить
        время на запрос к модели если вопрос уже есть в истории.
        """
        h = question_hash(question)
        existing = await self._get_by_hash(h)
        return existing is not None

    async def _get_by_hash(self, h: str) -> GeneratedContent | None:
        result = await self.s.execute(
            select(GeneratedContent).where(GeneratedContent.hash == h).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, record_id: int) -> GeneratedContent | None:
        result = await self.s.execute(
            select(GeneratedContent).where(GeneratedContent.id == record_id)
        )
        return result.scalar_one_or_none()

    # ── Выборки для аналитики ──────────────────────────────────────────────

    async def get_recent(self, limit: int = 20) -> list[GeneratedContent]:
        result = await self.s.execute(
            select(GeneratedContent)
            .order_by(desc(GeneratedContent.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_unwritten(self) -> list[GeneratedContent]:
        """Записи, ещё не попавшие в history.md (для восстановления после краша)."""
        result = await self.s.execute(
            select(GeneratedContent)
            .where(GeneratedContent.written_to_md == False)  # noqa: E712
            .order_by(GeneratedContent.created_at)
        )
        return list(result.scalars().all())

    async def get_by_lesson(self, lesson_id: int) -> list[GeneratedContent]:
        result = await self.s.execute(
            select(GeneratedContent)
            .where(GeneratedContent.lesson_id == lesson_id)
            .order_by(desc(GeneratedContent.created_at))
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        from sqlalchemy import func
        result = await self.s.execute(
            select(func.count()).select_from(GeneratedContent)
        )
        return result.scalar_one()

    async def get_recent_hashes(self, limit: int = 100) -> set[str]:
        """Возвращает set последних hash для быстрой предварительной проверки."""
        result = await self.s.execute(
            select(GeneratedContent.hash)
            .order_by(desc(GeneratedContent.created_at))
            .limit(limit)
        )
        return set(result.scalars().all())
