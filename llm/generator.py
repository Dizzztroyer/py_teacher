"""
llm/generator.py — генерация учебного контента через Qwen3 (Ollama).

Оптимизировано для скорости:
  - Короткие, плотные промпты (меньше токенов → быстрее)
  - Системный промпт сведён к минимуму
  - JSON-схема компактная, без verbose-описаний
  - Qwen3 хорошо понимает русский язык без лишних инструкций
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from llm.ollama_client import OllamaClient
from storage.models import Lesson

logger = logging.getLogger(__name__)


# ── Системный промпт — минимально необходимый ─────────────────────────────
# Qwen3 понимает роль с первых слов. Лишний текст = лишние токены = медленнее.

SYSTEM = (
    "Ты — преподаватель Python. "
    "Отвечай ТОЛЬКО валидным JSON без markdown. "
    "Язык ответа: русский."
)

# ── JSON-схема (компактная) ────────────────────────────────────────────────
# Одна схема для обоих типов промптов — меньше дублирования, легче парсить.

_SCHEMA = """\
{
  "theory": "<b>Тема</b>\\n\\nHTML-объяснение 2-4 строки",
  "questions": [
    "Вопрос 1 (концепция)",
    "Вопрос 2 (пример)",
    "Вопрос 3 с кодом: `print(...)`"
  ],
  "poll_question": "Вопрос с одним верным ответом?",
  "poll_options": ["Вариант A", "Вариант B", "Вариант C", "Вариант D"],
  "correct_option_id": 0,
  "explanation": "Почему правильный ответ — A (1 предложение)",
  "difficulty": "easy"
}"""

# ── Шаблоны промптов (плотные, без воды) ──────────────────────────────────

_TMPL_LESSON = """\
УРОК: {source_file}
ТЕМЫ: {topics}
СЛОВА: {keywords}
РЕЗЮМЕ: {summary}
ТЕКСТ (фрагмент):
{excerpt}

НЕ ПОВТОРЯТЬ вопросы:
{used}

Сгенерируй учебный блок по уроку выше.
difficulty: easy|medium|hard — выбери сам.
Верни JSON:
{schema}"""

_TMPL_TOPIC = """\
ТЕМА: {title}
ОПИСАНИЕ: {description}
СЛОВА: {keywords}

НЕ ПОВТОРЯТЬ вопросы:
{used}

Сгенерируй учебный блок.
difficulty: easy|medium|hard — выбери сам.
Верни JSON:
{schema}"""


# ── Результат ─────────────────────────────────────────────────────────────

@dataclass
class GeneratedContent:
    theory: str
    questions: list[str]
    poll_question: str
    poll_options: list[str]
    correct_option_id: int
    explanation: str
    difficulty: str
    source_lesson_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ── Парсинг и валидация ────────────────────────────────────────────────────

def _strip_think_tags(text: str) -> str:
    """
    Qwen3 иногда оборачивает размышления в <think>...</think>.
    Убираем этот блок перед поиском JSON.
    """
    return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()


def _extract_json(text: str) -> dict:
    """Извлекает первый JSON-объект из текста ответа модели."""
    # Убираем think-блоки Qwen3
    text = _strip_think_tags(text)
    # Убираем ```json ... ``` обёртки (на всякий случай)
    text = re.sub(r"```(?:json)?", "", text).strip()
    text = text.replace("```", "")
    # Ищем первый { ... }
    match = re.search(r"\{[\s\S]+\}", text)
    if not match:
        raise ValueError(f"JSON не найден. Начало ответа: {text[:150]!r}")
    return json.loads(match.group(0))


def _validate(data: dict) -> None:
    """Проверяет структуру распаршенного JSON."""
    if not isinstance(data.get("theory"), str) or not data["theory"]:
        raise ValueError("theory: ожидается непустая строка")
    qs = data.get("questions")
    if not isinstance(qs, list) or len(qs) < 2:
        raise ValueError(f"questions: ожидается список ≥2 элементов, получено: {qs!r}")
    if not isinstance(data.get("poll_question"), str) or not data["poll_question"]:
        raise ValueError("poll_question: ожидается непустая строка")
    opts = data.get("poll_options", [])
    if not isinstance(opts, list) or not (2 <= len(opts) <= 10):
        raise ValueError(f"poll_options: ожидается 2-10 вариантов, получено: {len(opts)}")
    cid = data.get("correct_option_id")
    if not isinstance(cid, int) or not (0 <= cid < len(opts)):
        raise ValueError(f"correct_option_id={cid!r} вне диапазона [0, {len(opts)-1}]")


def _fallback(source_name: str, lesson_id: int | None = None) -> GeneratedContent:
    """Статический контент если Ollama недоступна или все retry провалились."""
    return GeneratedContent(
        theory=f"<b>{source_name}</b>\n\nМатериал по данной теме Python.",
        questions=[
            f"Что такое {source_name} и для чего используется?",
            f"Приведите пример использования {source_name} в реальном коде.",
            f"Какую ошибку допускают новички при работе с {source_name}?",
        ],
        poll_question=f"Что верно относительно {source_name}?",
        poll_options=["Правильное утверждение", "Неверное A", "Неверное B", "Неверное C"],
        correct_option_id=0,
        explanation="Правильный ответ — первый вариант.",
        difficulty="medium",
        source_lesson_id=lesson_id,
    )


def _fmt_used(used: list[str]) -> str:
    """Форматирует список использованных вопросов для промпта."""
    if not used:
        return "—"
    # Берём последние 20, обрезаем каждый до 80 символов — экономия токенов
    lines = [f"- {q[:80]}" for q in used[-20:]]
    return "\n".join(lines)


def _excerpt(text: str, max_chars: int = 1800) -> str:
    """Обрезает текст урока до max_chars символов."""
    if len(text) <= max_chars:
        return text
    # Обрезаем на границе слова
    cut = text[:max_chars]
    last_space = cut.rfind(" ")
    return cut[:last_space] + "…" if last_space > 0 else cut


# ── Основной класс ─────────────────────────────────────────────────────────

class ContentGenerator:
    """Генерирует учебный контент через Qwen3 (Ollama localhost)."""

    def __init__(self) -> None:
        self.client = OllamaClient()

    async def from_lesson(
        self,
        lesson: Lesson,
        used_questions: list[str],
    ) -> GeneratedContent:
        """Генерация по материалам PDF-урока."""
        topics = json.loads(lesson.topics or "[]")
        keywords = json.loads(lesson.keywords or "[]")

        prompt = _TMPL_LESSON.format(
            source_file=lesson.source_file,
            topics=", ".join(topics[:6]),
            keywords=", ".join(keywords[:12]),   # не больше 12 слов
            summary=lesson.summary[:600],        # обрезаем summary
            excerpt=_excerpt(lesson.raw_text),
            used=_fmt_used(used_questions),
            schema=_SCHEMA,
        )
        return await self._run(prompt, lesson.source_file, lesson.id)

    async def from_topic(
        self,
        topic: dict,
        used_questions: list[str],
    ) -> GeneratedContent:
        """Генерация по встроенной теме (без PDF)."""
        prompt = _TMPL_TOPIC.format(
            title=topic["title"],
            description=topic.get("description", "")[:300],
            keywords=topic.get("keywords", "")[:200],
            used=_fmt_used(used_questions),
            schema=_SCHEMA,
        )
        return await self._run(prompt, topic["title"], None)

    async def _run(
        self,
        prompt: str,
        source_name: str,
        lesson_id: int | None,
    ) -> GeneratedContent:
        """
        Запускает генерацию с retry.
        Retry здесь — на уровне парсинга JSON (сетевые retry — в OllamaClient).
        """
        from config import settings
        retries = settings.LLM_RETRIES

        logger.info(
            "→ LLM generate | source=%r | prompt_len=%d | model=%s",
            source_name, len(prompt), self.client.model,
        )

        for attempt in range(1, retries + 1):
            try:
                raw_text = await self.client.generate(prompt, system=SYSTEM)

                data = _extract_json(raw_text)
                _validate(data)

                content = GeneratedContent(
                    theory=data["theory"],
                    questions=data["questions"],
                    poll_question=data["poll_question"][:300],
                    poll_options=[o[:100] for o in data["poll_options"][:10]],
                    correct_option_id=data["correct_option_id"],
                    explanation=data.get("explanation", "")[:200],
                    difficulty=data.get("difficulty", "medium"),
                    source_lesson_id=lesson_id,
                    raw=data,
                )

                logger.info(
                    "✅ Контент готов | попытка=%d | вопросов=%d | difficulty=%s",
                    attempt, len(content.questions), content.difficulty,
                )
                return content

            except RuntimeError as exc:
                # OllamaClient уже исчерпал свои retry → сразу fallback
                logger.error("Ollama недоступна: %s → fallback", exc)
                return _fallback(source_name, lesson_id)

            except (ValueError, KeyError, AssertionError, json.JSONDecodeError) as exc:
                logger.warning(
                    "Парсинг JSON, попытка %d/%d: %s", attempt, retries, exc
                )
                if attempt == retries:
                    logger.error("Все попытки парсинга исчерпаны → fallback")
                    return _fallback(source_name, lesson_id)
                # Небольшая пауза перед повтором
                import asyncio
                await asyncio.sleep(settings.LLM_RETRY_DELAY)

        return _fallback(source_name, lesson_id)
