"""
bot/handlers.py — обработчики команд Telegram.

Команды:
  /start
  /question [latest|review|mixed|topic:<name>]
  /ingest_latest
  /lessons
  /lesson_status
  /regenerate
  /topics
  /status
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import settings
from content.lesson_ingestor import LessonIngestor
from logic.lesson_service import LessonService
from storage.db import get_session
from storage.repository import LessonRepo, QuestionRepo

logger = logging.getLogger(__name__)
router = Router()


# ── /start ─────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 <b>Python Teacher Bot</b>\n\n"
        "Я генерирую вопросы по урокам Python — из PDF-материалов или встроенного банка тем.\n\n"
        "<b>Команды:</b>\n"
        "  /question — вопрос (режим по умолчанию)\n"
        "  /question latest — по последнему уроку\n"
        "  /question review — повторение старых уроков\n"
        "  /question mixed — смешанный режим\n"
        "  /ingest_latest — обработать новый PDF из materials/\n"
        "  /lessons — список загруженных уроков\n"
        "  /lesson_status — статус последнего урока\n"
        "  /regenerate — перегенерировать последний вопрос\n"
        "  /topics — встроенные темы\n"
        "  /history — история вопросов\n"
        "  /status — статус бота"
    )


# ── /question [mode] ───────────────────────────────────────────────────────

@router.message(Command("question"))
async def cmd_question(message: Message) -> None:
    """
    Синтаксис:
      /question
      /question latest
      /question review
      /question mixed
      /question topic:LEGB
    """
    # Разбираем аргумент
    text = message.text or ""
    parts = text.strip().split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    if not arg or arg == "mixed":
        mode, topic_name = "mixed", None
    elif arg == "latest":
        mode, topic_name = "latest", None
    elif arg == "review":
        mode, topic_name = "review", None
    elif arg.startswith("topic:"):
        mode = "topic"
        topic_name = arg[6:].strip()
    else:
        await message.answer(
            "❓ Неизвестный режим. Используй: latest | review | mixed | topic:<name>"
        )
        return

    await message.answer(f"⏳ Генерирую вопрос (режим: <b>{mode}</b>)...")
    try:
        service = LessonService()
        await service.send_lesson(message.bot, message.chat.id, mode=mode, topic_name=topic_name)
    except Exception as exc:
        logger.exception("Ошибка /question: %s", exc)
        await message.answer(f"❌ Ошибка: <code>{exc}</code>")


# ── /ingest_latest ─────────────────────────────────────────────────────────

@router.message(Command("ingest_latest"))
async def cmd_ingest_latest(message: Message) -> None:
    """Обрабатывает последний PDF из папки materials/."""
    await message.answer(f"🔍 Ищу PDF в папке <code>{settings.MATERIALS_DIR}/</code>...")
    try:
        ingestor = LessonIngestor()
        pdfs = ingestor.list_pdf_files()

        if not pdfs:
            await message.answer(
                f"📁 В папке <code>{settings.MATERIALS_DIR}/</code> нет PDF-файлов.\n\n"
                "Положи PDF в эту папку и повтори команду."
            )
            return

        await message.answer(f"📄 Найден файл: <code>{pdfs[0].name}</code>\nОбрабатываю...")
        result = await ingestor.ingest_latest()

        if result.already_exists:
            await message.answer(
                f"ℹ️ Урок <code>{result.source_file}</code> уже был загружен ранее (id={result.lesson_id}).\n"
                f"Используй /lesson_status для деталей."
            )
        elif result.success:
            # Показываем детали урока
            async with get_session() as session:
                repo = LessonRepo(session)
                lesson = await repo.get_latest()

            topics = json.loads(lesson.topics or "[]") if lesson else []
            keywords = json.loads(lesson.keywords or "[]") if lesson else []

            await message.answer(
                f"✅ <b>Урок загружен!</b>\n\n"
                f"📄 Файл: <code>{result.source_file}</code>\n"
                f"🆔 ID: {result.lesson_id}\n"
                f"📑 Страниц: {lesson.page_count if lesson else '?'}\n\n"
                f"📌 <b>Темы:</b> {', '.join(topics[:5]) or 'не определены'}\n"
                f"🔑 <b>Ключевые слова:</b> {', '.join(keywords[:8]) or 'не определены'}\n\n"
                f"📝 <b>Summary:</b>\n{lesson.summary[:500] if lesson else ''}\n\n"
                f"<i>Теперь /question latest — вопросы по этому уроку</i>"
            )
        else:
            await message.answer(f"❌ Ошибка обработки: <code>{result.error}</code>")

    except Exception as exc:
        logger.exception("Ошибка /ingest_latest: %s", exc)
        await message.answer(f"❌ Ошибка: <code>{exc}</code>")


# ── /lessons ───────────────────────────────────────────────────────────────

@router.message(Command("lessons"))
async def cmd_lessons(message: Message) -> None:
    """Показывает список всех загруженных уроков."""
    async with get_session() as session:
        repo = LessonRepo(session)
        lessons = await repo.get_all()

    if not lessons:
        await message.answer(
            "📭 Уроков пока нет.\n\n"
            "Положи PDF-файл в папку <code>materials/</code> и выполни /ingest_latest"
        )
        return

    lines = []
    for i, lesson in enumerate(lessons[:20], 1):
        topics = json.loads(lesson.topics or "[]")
        dt = lesson.ingested_at.strftime("%d.%m.%Y %H:%M") if lesson.ingested_at else "?"
        lines.append(
            f"<b>{i}.</b> <code>{lesson.source_file}</code>\n"
            f"   🆔 id={lesson.id} | 📅 {dt} UTC | 📑 {lesson.page_count} стр.\n"
            f"   📌 {', '.join(topics[:3]) or 'без тем'}"
        )

    await message.answer(
        f"📚 <b>Загруженные уроки ({len(lessons)}):</b>\n\n" + "\n\n".join(lines)
    )


# ── /lesson_status ─────────────────────────────────────────────────────────

@router.message(Command("lesson_status"))
async def cmd_lesson_status(message: Message) -> None:
    """Показывает детали последнего загруженного урока."""
    async with get_session() as session:
        repo = LessonRepo(session)
        lesson = await repo.get_latest()
        fresh = await repo.get_fresh()
        total = await repo.count()

    if not lesson:
        await message.answer(
            "📭 Уроков пока нет. Используй /ingest_latest для загрузки PDF."
        )
        return

    topics = json.loads(lesson.topics or "[]")
    keywords = json.loads(lesson.keywords or "[]")
    dt = lesson.ingested_at.strftime("%d.%m.%Y %H:%M") if lesson.ingested_at else "?"
    is_fresh = "✅ свежий" if fresh and fresh.id == lesson.id else "📦 архивный"

    await message.answer(
        f"📄 <b>Последний урок</b> ({is_fresh})\n\n"
        f"🆔 ID: {lesson.id}\n"
        f"📁 Файл: <code>{lesson.source_file}</code>\n"
        f"📅 Загружен: {dt} UTC\n"
        f"📑 Страниц: {lesson.page_count}\n\n"
        f"📌 <b>Темы:</b>\n"
        + "\n".join(f"  • {t}" for t in topics) + "\n\n"
        f"🔑 <b>Ключевые слова:</b>\n"
        f"{', '.join(keywords[:15])}\n\n"
        f"📝 <b>Summary:</b>\n{lesson.summary[:600]}\n\n"
        f"<i>Всего уроков в базе: {total}</i>"
    )


# ── /regenerate ────────────────────────────────────────────────────────────

@router.message(Command("regenerate"))
async def cmd_regenerate(message: Message) -> None:
    """Перегенерирует вопрос в том же режиме, что и последний."""
    await message.answer("🔄 Перегенерирую вопрос...")
    try:
        service = LessonService()
        # Используем latest если есть свежий урок, иначе mixed
        async with get_session() as session:
            fresh = await LessonRepo(session).get_fresh()
        mode = "latest" if fresh else "mixed"
        await service.send_lesson(message.bot, message.chat.id, mode=mode)
    except Exception as exc:
        logger.exception("Ошибка /regenerate: %s", exc)
        await message.answer(f"❌ Ошибка: <code>{exc}</code>")


# ── /topics ────────────────────────────────────────────────────────────────

@router.message(Command("topics"))
async def cmd_topics(message: Message) -> None:
    from logic.topics import TOPICS
    lines = "\n".join(f"  {i+1}. {t['title']}" for i, t in enumerate(TOPICS))
    await message.answer(
        f"📚 <b>Встроенные темы ({len(TOPICS)}):</b>\n{lines}\n\n"
        f"<i>Используются если нет PDF-уроков</i>"
    )


# ── /status ────────────────────────────────────────────────────────────────

@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    from llm.ollama_client import OllamaClient
    client = OllamaClient()
    ollama_ok = await client.check_connection()
    ollama_str = "✅ доступна" if ollama_ok else "❌ недоступна"

    async with get_session() as session:
        repo = LessonRepo(session)
        total_lessons = await repo.count()
        latest = await repo.get_latest()
        fresh = await repo.get_fresh()
        q_repo = QuestionRepo(session)
        used_q = await q_repo.get_recent(limit=5)

    latest_str = f"<code>{latest.source_file}</code>" if latest else "нет"
    fresh_str = "✅ есть" if fresh else "нет"

    post_times = " и ".join(
        f"{h:02d}:{m:02d}" for h, m in settings.post_times_parsed
    )

    await message.answer(
        f"🤖 <b>Python Teacher Bot</b>\n\n"
        f"📡 Ollama: {ollama_str}\n"
        f"   Модель: <code>{settings.OLLAMA_MODEL}</code>\n\n"
        f"⏰ Расписание: {post_times} UTC\n"
        f"📅 Дни урока: {settings.LESSON_DAYS}\n"
        f"🔄 Режим по умолчанию: {settings.DEFAULT_MODE}\n\n"
        f"📚 Уроков в БД: {total_lessons}\n"
        f"📄 Последний: {latest_str}\n"
        f"🆕 Свежий (≤{settings.FRESH_LESSON_HOURS}ч): {fresh_str}\n"
        f"❓ Использовано вопросов: {len(used_q)} (показаны последние 5)"
    )


# ── /history ────────────────────────────────────────────────────────────────

@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    """Показывает последние записи из истории + статистику."""
    from storage.content_repo import GeneratedContentRepo
    from storage.history_writer import history_writer
    import json as _json

    async with get_session() as session:
        repo = GeneratedContentRepo(session)
        records = await repo.get_recent(limit=5)
        total = await repo.count()

    if not records:
        await message.answer(
            "📭 История пуста.\n\nСгенерируй первый вопрос: /question"
        )
        return

    # Список файлов истории
    md_files = history_writer.list_history_files()
    md_info = ""
    if md_files:
        sizes = [f"{p.name} ({p.stat().st_size // 1024} КБ)" for p in md_files[:3]]
        md_info = "\n📁 <b>Файлы истории:</b>\n" + "\n".join(f"  • {s}" for s in sizes)

    # Последние записи
    lines = []
    for r in records:
        dt = r.created_at.strftime("%d.%m.%Y %H:%M")
        try:
            opts = _json.loads(r.options_json)
            letters = "ABCDEFGHIJ"
            correct = (
                f"{letters[r.correct_option]}: {opts[r.correct_option]}"
                if r.correct_option < len(opts) else "?"
            )
        except Exception:
            correct = "?"
        lines.append(
            f"<b>#{r.id}</b> [{dt}] <i>{r.source_name[:30]}</i> | {r.difficulty}\n"
            f"  ❓ {r.question[:80]}…\n"
            f"  ✅ {correct[:60]}"
        )

    await message.answer(
        f"📊 <b>История вопросов</b>\n"
        f"Всего записей: <b>{total}</b>\n"
        f"{md_info}\n\n"
        f"<b>Последние {len(records)}:</b>\n\n"
        + "\n\n".join(lines)
    )
