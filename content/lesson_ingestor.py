"""
content/lesson_ingestor.py — ingestion pipeline: PDF → Lesson в БД.

Шаги:
  1. Сканировать папку materials/
  2. Найти новые PDF (не в БД)
  3. Прочитать и очистить текст
  4. Сгенерировать summary и topics через Ollama
  5. Сохранить Lesson в БД
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config import settings
from content.pdf_reader import PdfReadResult, read_pdf
from llm.ollama_client import OllamaClient
from storage.db import get_session
from storage.models import Lesson
from storage.repository import LessonRepo

logger = logging.getLogger(__name__)


# ── Ollama-запрос для summary ──────────────────────────────────────────────

_SUMMARIZE_SYSTEM = """Ты — методист курса Python.
Отвечай ТОЛЬКО валидным JSON без markdown-обёрток и пояснений."""

_SUMMARIZE_PROMPT = """Проанализируй текст урока Python ниже и верни JSON строго по схеме:

{{
  "summary": "Краткое резюме урока (3-5 предложений на русском языке)",
  "topics": ["тема 1", "тема 2", "тема 3"],
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"]
}}

Требования:
- summary: на русском, 3-5 предложений, суть урока
- topics: 3-7 ключевых тем урока
- keywords: 5-15 технических терминов из урока

ТЕКСТ УРОКА (первые 4000 символов):
{text}"""


async def _generate_summary(text: str) -> dict:
    """Запрашивает у Ollama summary + topics + keywords для урока."""
    client = OllamaClient()
    excerpt = text[:4000]
    prompt = _SUMMARIZE_PROMPT.format(text=excerpt)

    for attempt in range(1, 4):
        try:
            raw = await client.generate(prompt, system=_SUMMARIZE_SYSTEM)
            # Убираем возможные ```json``` обёртки
            raw = re.sub(r"```(?:json)?", "", raw).strip()
            match = re.search(r"\{[\s\S]+\}", raw)
            if not match:
                raise ValueError("JSON не найден в ответе")
            data = json.loads(match.group(0))
            assert isinstance(data.get("summary"), str)
            assert isinstance(data.get("topics"), list)
            assert isinstance(data.get("keywords"), list)
            return data
        except Exception as exc:
            logger.warning("summary attempt %d/3 failed: %s", attempt, exc)
            await asyncio.sleep(1)

    # Fallback если Ollama недоступна
    logger.warning("Использую fallback summary")
    return {
        "summary": "Урок Python. Подробности в материалах.",
        "topics": ["Python"],
        "keywords": [],
    }


# ── Dataclass результата ───────────────────────────────────────────────────

@dataclass
class IngestResult:
    success: bool
    lesson_id: int | None = None
    source_file: str = ""
    error: str = ""
    already_exists: bool = False


# ── Основной класс ─────────────────────────────────────────────────────────

class LessonIngestor:
    """Обрабатывает PDF и сохраняет уроки в БД."""

    def __init__(self) -> None:
        self.materials_dir = settings.MATERIALS_DIR
        self.materials_dir.mkdir(parents=True, exist_ok=True)

    def list_pdf_files(self) -> list[Path]:
        """Возвращает список PDF в папке materials/, отсортированный по дате изменения."""
        files = sorted(
            self.materials_dir.glob("*.pdf"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return files

    async def find_new_pdfs(self) -> list[Path]:
        """Возвращает PDF, которых ещё нет в БД."""
        all_pdfs = self.list_pdf_files()
        new_pdfs = []
        async with get_session() as session:
            repo = LessonRepo(session)
            for pdf in all_pdfs:
                existing = await repo.get_by_source(pdf.name)
                if existing is None:
                    new_pdfs.append(pdf)
        return new_pdfs

    async def ingest_file(self, pdf_path: Path) -> IngestResult:
        """Полный pipeline для одного PDF."""
        logger.info("Начинаю ingestion: %s", pdf_path.name)

        # Проверяем, нет ли уже в БД
        async with get_session() as session:
            repo = LessonRepo(session)
            existing = await repo.get_by_source(pdf_path.name)
            if existing is not None:
                logger.info("Урок уже существует: %s (id=%d)", pdf_path.name, existing.id)
                return IngestResult(
                    success=True,
                    lesson_id=existing.id,
                    source_file=pdf_path.name,
                    already_exists=True,
                )

        # Чтение PDF (синхронное, в executor)
        loop = asyncio.get_event_loop()
        result: PdfReadResult = await loop.run_in_executor(None, read_pdf, pdf_path)

        if not result.ok:
            logger.error("Ошибка чтения PDF: %s", result.error)
            return IngestResult(success=False, source_file=pdf_path.name, error=result.error)

        # Генерация summary через Ollama
        logger.info("Генерирую summary через Ollama...")
        meta = await _generate_summary(result.clean_text)

        # Сохранение в БД
        async with get_session() as session:
            repo = LessonRepo(session)
            lesson = Lesson(
                source_file=pdf_path.name,
                ingested_at=datetime.utcnow(),
                raw_text=result.clean_text,
                summary=meta["summary"],
                topics=json.dumps(meta["topics"], ensure_ascii=False),
                keywords=json.dumps(
                    list(set(result.keywords + meta["keywords"]))[:30],
                    ensure_ascii=False,
                ),
                page_count=result.page_count,
            )
            lesson = await repo.save(lesson)
            lesson_id = lesson.id

        logger.info(
            "✅ Урок сохранён: id=%d | файл=%s | страниц=%d | summary=%d символов",
            lesson_id, pdf_path.name, result.page_count, len(meta["summary"]),
        )
        return IngestResult(success=True, lesson_id=lesson_id, source_file=pdf_path.name)

    async def ingest_latest(self) -> IngestResult:
        """Обработать самый новый PDF из папки materials/."""
        pdfs = self.list_pdf_files()
        if not pdfs:
            return IngestResult(
                success=False,
                error=f"Нет PDF-файлов в папке {self.materials_dir}/",
            )
        return await self.ingest_file(pdfs[0])

    async def ingest_all_new(self) -> list[IngestResult]:
        """Обработать все ещё не загруженные PDF."""
        new_pdfs = await self.find_new_pdfs()
        if not new_pdfs:
            logger.info("Нет новых PDF для обработки")
            return []
        results = []
        for pdf in new_pdfs:
            r = await self.ingest_file(pdf)
            results.append(r)
        return results
