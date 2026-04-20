"""
storage/history_writer.py — запись истории в Markdown-файл.

Правила (жёсткие):
  ✅ ТОЛЬКО добавление (append), НИКОГДА перезапись
  ✅ UTF-8 кодировка всегда
  ✅ Файл может расти неограниченно — это нормально
  ✅ Ротация по месяцам: history_2026_04.md (опционально, включается в .env)
  ✅ Дедупликация — ТОЛЬКО через БД, не через этот файл

Структура записи:
  ## 2026-04-19 14:35:22 UTC | ID: 42 | Источник: lesson_01.pdf | easy

  **Вопрос**
  Что такое list comprehension в Python?

  **Опрос**
  Какой синтаксис list comprehension корректен?

  **Варианты**
  - A: [x for x in range(10)]  ✅
  - B: {x for x in range(10)}
  - C: (x for x in range(10))
  - D: x for x in range(10)

  **Правильный ответ**
  0 → A: [x for x in range(10)]

  **Объяснение**
  Квадратные скобки создают list, фигурные — set, круглые — generator.

  ---
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from config import settings
from storage.models import GeneratedContent

logger = logging.getLogger(__name__)

# Константа директории для MD-файлов
_HISTORY_DIR = Path("data/history")


def _get_history_path(rotate_monthly: bool = True) -> Path:
    """
    Возвращает путь к текущему файлу истории.

    rotate_monthly=True  → data/history/history_2026_04.md
    rotate_monthly=False → data/history/history.md
    """
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    if rotate_monthly:
        now = datetime.utcnow()
        filename = f"history_{now.year}_{now.month:02d}.md"
    else:
        filename = "history.md"
    return _HISTORY_DIR / filename


def _format_options(options: list[str], correct_idx: int) -> str:
    """Форматирует варианты ответов с пометкой правильного."""
    letters = "ABCDEFGHIJ"
    lines = []
    for i, opt in enumerate(options):
        letter = letters[i] if i < len(letters) else str(i)
        marker = " ✅" if i == correct_idx else ""
        lines.append(f"- {letter}: {opt}{marker}")
    return "\n".join(lines)


def _format_record(record: GeneratedContent, options: list[str]) -> str:
    """
    Форматирует одну запись истории в Markdown.

    Никогда не вызывает truncate/seek/write с offset=0.
    Только строит строку для последующего append.
    """
    dt = record.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    lesson_str = record.source_name or "встроенная тема"
    letters = "ABCDEFGHIJ"
    correct_letter = (
        letters[record.correct_option]
        if record.correct_option < len(letters)
        else str(record.correct_option)
    )
    correct_text = (
        options[record.correct_option]
        if record.correct_option < len(options)
        else "?"
    )

    return (
        f"\n## {dt} | ID: {record.id} | Источник: {lesson_str} | {record.difficulty}\n"
        f"\n**Вопрос**\n"
        f"{record.question}\n"
        f"\n**Опрос**\n"
        f"{record.poll_question}\n"
        f"\n**Варианты**\n"
        f"{_format_options(options, record.correct_option)}\n"
        f"\n**Правильный ответ**\n"
        f"{record.correct_option} → {correct_letter}: {correct_text}\n"
        f"\n**Объяснение**\n"
        f"{record.explanation or '—'}\n"
        f"\n---\n"
    )


def _write_header_if_new(path: Path) -> None:
    """Добавляет заголовок в начало нового файла (только если файл не существует)."""
    if not path.exists() or path.stat().st_size == 0:
        now = datetime.utcnow()
        header = (
            f"# Python Teacher Bot — История вопросов\n"
            f"# Создан: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"# Файл: {path.name}\n"
            f"# ВНИМАНИЕ: Файл только для чтения. Не редактируй вручную.\n"
            f"# Дедупликация выполняется через SQLite, не через этот файл.\n\n"
        )
        # Используем 'w' только для создания нового файла (пустого)
        path.write_text(header, encoding="utf-8")
        logger.info("Создан новый файл истории: %s", path)


class HistoryWriter:
    """
    Записывает историю вопросов в Markdown-файл.

    Thread-safe через asyncio.Lock (один файл на процесс).
    Использует исключительно режим 'a' (append) — никогда 'w'.
    """

    def __init__(self, rotate_monthly: bool = True) -> None:
        self.rotate_monthly = rotate_monthly
        self._lock = asyncio.Lock()

    async def append(self, record: GeneratedContent) -> Path:
        """
        Добавляет запись в конец файла истории.

        Никогда не перезаписывает существующее содержимое.
        Возвращает путь к файлу куда была записана запись.

        Args:
            record: объект GeneratedContent из БД (уже сохранённый)
        """
        import json as _json

        try:
            options: list[str] = _json.loads(record.options_json)
        except Exception:
            options = []

        formatted = _format_record(record, options)
        path = _get_history_path(self.rotate_monthly)

        async with self._lock:
            # Запускаем синхронный IO в executor чтобы не блокировать event loop
            await asyncio.get_event_loop().run_in_executor(
                None,
                self._sync_append,
                path,
                formatted,
            )

        logger.info(
            "📝 История: запись id=%d добавлена в %s (%d байт)",
            record.id, path.name, len(formatted.encode("utf-8")),
        )
        return path

    @staticmethod
    def _sync_append(path: Path, text: str) -> None:
        """
        Синхронная запись — вызывается из executor.

        КРИТИЧНО:
          - режим 'a' (append) — open НИКОГДА не усекает файл
          - encoding='utf-8' — всегда явно
          - errors='replace' — защита от редких символов
        """
        _write_header_if_new(path)
        with path.open("a", encoding="utf-8", errors="replace") as f:
            f.write(text)

    async def append_bulk(self, records: list[GeneratedContent]) -> None:
        """Записывает несколько записей за одну операцию (для восстановления)."""
        for record in records:
            await self.append(record)

    def get_current_path(self) -> Path:
        """Возвращает путь к текущему файлу истории (без создания)."""
        return _get_history_path(self.rotate_monthly)

    def list_history_files(self) -> list[Path]:
        """Возвращает все файлы истории, отсортированные по дате (новые первыми)."""
        if not _HISTORY_DIR.exists():
            return []
        files = sorted(
            _HISTORY_DIR.glob("history*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return files


# ── Singleton для использования во всём приложении ─────────────────────────
history_writer = HistoryWriter(rotate_monthly=True)
