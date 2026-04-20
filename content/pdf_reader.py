"""
content/pdf_reader.py — чтение и очистка текста из PDF (pypdf).

Поддерживает русскоязычные PDF.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader

logger = logging.getLogger(__name__)


# ── Очистка текста ─────────────────────────────────────────────────────────

_MULTI_NEWLINE = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_PAGE_NUMBER = re.compile(r"^\s*\d+\s*$", re.MULTILINE)
_HEADER_FOOTER = re.compile(r"^.{1,80}(?:страница|page|\bстр\b).*$", re.MULTILINE | re.IGNORECASE)
_SOFT_HYPHEN = re.compile(r"\xad")  # мягкий перенос
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_text(raw: str) -> str:
    """Очищает сырой текст из PDF."""
    # Нормализация Unicode
    text = unicodedata.normalize("NFC", raw)
    # Убираем мягкие переносы и управляющие символы
    text = _SOFT_HYPHEN.sub("", text)
    text = _CONTROL_CHARS.sub(" ", text)
    # Убираем номера страниц (строка только из цифр)
    text = _PAGE_NUMBER.sub("", text)
    # Схлопываем лишние пробелы и переносы
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


def _extract_keywords(text: str, max_keywords: int = 30) -> list[str]:
    """
    Простое извлечение ключевых слов без внешних NLP-библиотек:
    находим все «технические» слова (CamelCase, snake_case, содержащие
    только латиницу, цифры и подчёркивание длиной 3+).
    """
    # Python-идентификаторы и camelCase термины
    pattern = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b")
    candidates: dict[str, int] = {}
    for match in pattern.finditer(text):
        word = match.group(1)
        # Пропускаем короткие и стоп-слова
        if word.lower() in _STOPWORDS:
            continue
        candidates[word] = candidates.get(word, 0) + 1

    # Сортируем по частоте
    sorted_words = sorted(candidates.items(), key=lambda x: -x[1])
    return [w for w, _ in sorted_words[:max_keywords]]


_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "are", "from",
    "not", "can", "will", "has", "have", "was", "you", "all",
    "def", "class", "import", "return", "None", "True", "False",
    "self", "args", "kwargs",
}


# ── Публичный API ──────────────────────────────────────────────────────────

class PdfReadResult:
    __slots__ = ("raw_text", "clean_text", "keywords", "page_count", "error")

    def __init__(
        self,
        raw_text: str = "",
        clean_text: str = "",
        keywords: list[str] | None = None,
        page_count: int = 0,
        error: str = "",
    ) -> None:
        self.raw_text = raw_text
        self.clean_text = clean_text
        self.keywords = keywords or []
        self.page_count = page_count
        self.error = error

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.clean_text)


def read_pdf(path: Path) -> PdfReadResult:
    """
    Синхронное чтение PDF (pypdf не поддерживает async).
    Вызывать из asyncio через loop.run_in_executor.
    """
    if not path.exists():
        return PdfReadResult(error=f"Файл не найден: {path}")
    if path.suffix.lower() != ".pdf":
        return PdfReadResult(error=f"Не PDF: {path}")

    try:
        reader = PdfReader(str(path))
        pages: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)

        raw = "\n".join(pages)
        clean = _clean_text(raw)

        if len(clean) < 50:
            return PdfReadResult(
                error=f"Слишком мало текста ({len(clean)} символов) — возможно, PDF со сканом"
            )

        keywords = _extract_keywords(clean)
        logger.info("PDF прочитан: %s | страниц=%d | символов=%d | ключ.слов=%d",
                    path.name, len(reader.pages), len(clean), len(keywords))

        return PdfReadResult(
            raw_text=raw,
            clean_text=clean,
            keywords=keywords,
            page_count=len(reader.pages),
        )

    except Exception as exc:
        logger.exception("Ошибка чтения PDF %s: %s", path, exc)
        return PdfReadResult(error=str(exc))
