"""
logic/lesson_service.py — оркестратор урока.

Pipeline после успешной генерации:
  1. Проверка дубля по hash (БД) → регенерация если дубль
  2. Сохранение в generated_content (SQLite) — источник правды
  3. Запись в history.md (append-only, UTF-8)
  4. Сохранение GeneratedItem + Poll + UsedQuestions
  5. Отправка в Telegram
  6. Пометка item как sent
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from typing import Literal

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from config import settings
from llm.generator import ContentGenerator, GeneratedContent
from logic.topics import pick_topic
from storage.content_repo import GeneratedContentRepo
from storage.db import get_session
from storage.hashing import question_hash
from storage.history_writer import history_writer
from storage.models import GeneratedItem, Poll
from storage.repository import ItemRepo, LessonRepo, PollRepo, QuestionRepo

logger = logging.getLogger(__name__)

Mode = Literal["latest", "review", "mixed", "topic"]

# Максимум попыток регенерации при обнаружении дубля
_MAX_REGEN = 3


class LessonService:
    """Оркестрирует полный цикл генерации и отправки урока."""

    def __init__(self) -> None:
        self.generator = ContentGenerator()

    # ── Публичный интерфейс ────────────────────────────────────────────────

    async def send_lesson(
        self,
        bot: Bot,
        chat_id: int,
        mode: Mode = "mixed",
        topic_name: str | None = None,
    ) -> None:
        """Полный цикл: генерация → проверка → сохранение → история → Telegram."""

        # Шаг 1: Получаем контент с защитой от дублей
        content, source_name = await self._get_unique_content(mode, topic_name)

        # Шаг 2: Сохраняем в generated_content + проверяем на дубль (финальная)
        async with get_session() as session:
            content_repo = GeneratedContentRepo(session)
            item_repo = ItemRepo(session)
            poll_repo = PollRepo(session)
            q_repo = QuestionRepo(session)

            # Сохранение в основную таблицу истории
            gc_record, is_new = await content_repo.save(
                question=content.questions[0],
                poll_question=content.poll_question,
                options=content.poll_options,
                correct_option=content.correct_option_id,
                explanation=content.explanation,
                difficulty=content.difficulty,
                lesson_id=content.source_lesson_id,
                source_name=source_name,
            )

            if not is_new:
                logger.warning(
                    "⚠️  Дубль после всех попыток (hash=%s…). Отправляю как есть.",
                    gc_record.hash[:12],
                )

            # GeneratedItem (расширенный, с theory и mode)
            item = GeneratedItem(
                lesson_id=content.source_lesson_id,
                content_id=gc_record.id,
                mode=mode,
                theory=content.theory,
                difficulty=content.difficulty,
            )
            item = await item_repo.save(item)

            # Poll
            poll = Poll(
                item_id=item.id,
                question=content.poll_question,
                options=json.dumps(content.poll_options, ensure_ascii=False),
                correct_option_id=content.correct_option_id,
                explanation=content.explanation,
            )
            await poll_repo.save(poll)

            # UsedQuestions (для LLM-контекста в следующих запросах)
            await q_repo.add_bulk(
                content.questions,
                item_id=item.id,
                lesson_id=content.source_lesson_id,
            )

            item_id = item.id
            gc_id = gc_record.id

        # Шаг 3: Запись в history.md (ТОЛЬКО append, НИКОГДА overwrite)
        if is_new:
            try:
                # Перечитываем свежую запись чтобы убедиться что id проставлен
                async with get_session() as session:
                    fresh_record = await GeneratedContentRepo(session).get_by_id(gc_id)
                if fresh_record:
                    md_path = await history_writer.append(fresh_record)
                    async with get_session() as session:
                        await GeneratedContentRepo(session).mark_written_to_md(gc_id)
                    logger.info("📝 Запись добавлена в %s", md_path.name)
            except Exception as exc:
                # Ошибка записи в MD не должна прерывать отправку в Telegram
                logger.error("Ошибка записи в history.md: %s", exc)

        # Шаг 4: Отправка в Telegram
        await self._send_theory(bot, chat_id, content)
        await self._send_questions(bot, chat_id, content)
        await self._send_poll(bot, chat_id, content)

        # Шаг 5: Пометить item как отправленный
        async with get_session() as session:
            await ItemRepo(session).mark_sent(item_id)

        logger.info(
            "✅ Урок отправлен | mode=%s gc_id=%d item_id=%d is_new=%s",
            mode, gc_id, item_id, is_new,
        )

    # ── Получение уникального контента ────────────────────────────────────

    async def _get_unique_content(
        self,
        mode: Mode,
        topic_name: str | None,
    ) -> tuple[GeneratedContent, str]:
        """
        Генерирует контент и проверяет на дубль ДО сохранения.
        Повторяет генерацию до _MAX_REGEN раз если обнаружен дубль.

        Returns:
            (content, source_name) — готовый уникальный контент и название источника.
        """
        for attempt in range(1, _MAX_REGEN + 1):
            content, source_name = await self._generate_by_mode(mode, topic_name)

            # Быстрая pre-check по hash (без сохранения)
            h = question_hash(content.questions[0])
            async with get_session() as session:
                repo = GeneratedContentRepo(session)
                is_dup = await repo.is_duplicate(content.questions[0])

            if not is_dup:
                if attempt > 1:
                    logger.info("✅ Уникальный контент получен на попытке %d", attempt)
                return content, source_name

            logger.warning(
                "🔁 Pre-check: дубль на попытке %d/%d (hash=%s…). Регенерирую...",
                attempt, _MAX_REGEN, h[:12],
            )

        # Если все попытки дали дубли — возвращаем последний (лучше дубль чем ничего)
        logger.error("Все %d попытки дали дубли, возвращаю последний результат", _MAX_REGEN)
        return content, source_name  # type: ignore[return-value]

    async def _generate_by_mode(
        self,
        mode: Mode,
        topic_name: str | None,
    ) -> tuple[GeneratedContent, str]:
        """Выбирает источник по режиму и генерирует контент."""
        async with get_session() as session:
            lesson_repo = LessonRepo(session)
            q_repo = QuestionRepo(session)
            used = await q_repo.get_recent()
            latest = await lesson_repo.get_latest()
            fresh = await lesson_repo.get_fresh()

        if mode == "latest":
            if latest:
                return await self.generator.from_lesson(latest, used), latest.source_file
            topic = pick_topic()
            return await self.generator.from_topic(topic, used), topic["title"]

        if mode == "review":
            async with get_session() as session:
                old = await LessonRepo(session).get_old_lessons(
                    exclude_id=latest.id if latest else None
                )
            if old:
                lesson = random.choice(old)
                return await self.generator.from_lesson(lesson, used), lesson.source_file
            if latest:
                return await self.generator.from_lesson(latest, used), latest.source_file
            topic = pick_topic()
            return await self.generator.from_topic(topic, used), topic["title"]

        if mode == "mixed":
            if fresh:
                return await self.generator.from_lesson(fresh, used), fresh.source_file
            if latest:
                async with get_session() as session:
                    old = await LessonRepo(session).get_old_lessons(exclude_id=latest.id)
                if old and random.random() < 0.5:
                    lesson = random.choice(old)
                    return await self.generator.from_lesson(lesson, used), lesson.source_file
                return await self.generator.from_lesson(latest, used), latest.source_file
            topic = pick_topic()
            return await self.generator.from_topic(topic, used), topic["title"]

        if mode == "topic" and topic_name:
            from logic.topics import TOPICS
            matched = next(
                (t for t in TOPICS if topic_name.lower() in t["title"].lower()), None
            )
            t = matched or pick_topic()
            return await self.generator.from_topic(t, used), t["title"]

        topic = pick_topic()
        return await self.generator.from_topic(topic, used), topic["title"]

    # ── Telegram отправка ──────────────────────────────────────────────────

    @staticmethod
    async def _send_theory(bot: Bot, chat_id: int, content: GeneratedContent) -> None:
        label = "📄 <b>По материалам урока</b>" if content.source_lesson_id else "📖 <b>Теория</b>"
        emoji = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}.get(content.difficulty, "🟡")
        try:
            await bot.send_message(chat_id=chat_id, text=f"{label} {emoji}\n\n{content.theory}")
        except TelegramAPIError as exc:
            logger.error("Ошибка отправки теории: %s", exc)
            raise

    @staticmethod
    async def _send_questions(bot: Bot, chat_id: int, content: GeneratedContent) -> None:
        numbered = "\n\n".join(
            f"<b>{i + 1}.</b> {q}" for i, q in enumerate(content.questions)
        )
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"❓ <b>Вопросы для размышления:</b>\n\n{numbered}",
            )
        except TelegramAPIError as exc:
            logger.error("Ошибка отправки вопросов: %s", exc)

    @staticmethod
    async def _send_poll(bot: Bot, chat_id: int, content: GeneratedContent) -> None:
        if len(content.poll_options) < 2:
            return
        try:
            await bot.send_poll(
                chat_id=chat_id,
                question=content.poll_question,
                options=content.poll_options,
                type="quiz",
                correct_option_id=content.correct_option_id,
                is_anonymous=True,
                explanation=content.explanation or "Разбор выше 👆",
            )
        except TelegramAPIError as exc:
            logger.error("Ошибка отправки опроса: %s", exc)
