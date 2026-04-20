"""
storage/hashing.py — функции хеширования для дедупликации контента.

Стратегия:
  - SHA-256 от нормализованного текста вопроса (точное совпадение)
  - Simhash от токенов (нечёткое совпадение, опционально)

Основной дубль-детектор использует SHA-256.
Нормализация убирает пунктуацию, лишние пробелы и приводит к нижнему регистру,
чтобы "Что такое list?" и "что такое list" давали одинаковый хеш.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata


# ── Нормализация ──────────────────────────────────────────────────────────

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)          # всё кроме букв/цифр/пробелов
_MULTI_WS = re.compile(r"\s+")                        # серии пробелов → один


def normalize(text: str) -> str:
    """
    Нормализует текст перед хешированием:
      1. NFC Unicode normalization
      2. lowercase
      3. убираем пунктуацию
      4. схлопываем пробелы
      5. strip
    """
    text = unicodedata.normalize("NFC", text)
    text = text.lower()
    text = _PUNCT.sub(" ", text)
    text = _MULTI_WS.sub(" ", text)
    return text.strip()


# ── SHA-256 хеш ───────────────────────────────────────────────────────────

def question_hash(question: str) -> str:
    """
    Возвращает SHA-256 hex-дайджест нормализованного вопроса.

    Используется как первичный ключ дедупликации в таблице generated_content.
    Длина: 64 символа (hex).

    Примеры:
      "Что такое list?"  →  sha256("что такое list")
      "Что такое list"   →  sha256("что такое list")   ← тот же хеш!
      "  ЧТО ТАКОЕ LIST?"→  sha256("что такое list")   ← тот же хеш!
    """
    normalized = normalize(question)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def content_hash(question: str, poll_question: str) -> str:
    """
    Хеш по комбинации вопроса + poll_question.
    Используется как дополнительный уникальный ключ для всего блока контента.
    """
    combined = normalize(question) + "||" + normalize(poll_question)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def questions_hash(questions: list[str]) -> str:
    """
    Хеш от ВСЕХ вопросов блока (упорядоченных).
    Позволяет детектировать дубли даже если переформулирован только один вопрос.
    """
    combined = "||".join(normalize(q) for q in sorted(questions))
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ── Simhash (нечёткое совпадение) ─────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Разбивает нормализованный текст на токены."""
    return normalize(text).split()


def simhash(text: str, bits: int = 64) -> int:
    """
    Simhash алгоритм для нечёткого сравнения.
    Возвращает целое число — fingerprint текста.

    Тексты с расстоянием Хэмминга ≤ 3 бита считаются похожими.
    Используется опционально, если SHA-256 miss'ит перефразированные вопросы.
    """
    v = [0] * bits
    tokens = _tokenize(text)

    for token in tokens:
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        for i in range(bits):
            bit = (h >> i) & 1
            v[i] += 1 if bit else -1

    fingerprint = 0
    for i in range(bits):
        if v[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    """Расстояние Хэмминга между двумя simhash fingerprint'ами."""
    xor = a ^ b
    # Алгоритм Brian Kernighan для подсчёта битов
    count = 0
    while xor:
        xor &= xor - 1
        count += 1
    return count


def is_similar_simhash(text_a: str, text_b: str, max_distance: int = 4) -> bool:
    """
    True если тексты семантически похожи по simhash.
    max_distance=4 — хорошее значение для вопросов длиной 5-15 слов.
    """
    return hamming_distance(simhash(text_a), simhash(text_b)) <= max_distance
