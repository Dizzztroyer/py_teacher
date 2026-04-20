"""
llm/ollama_client.py — async HTTP-клиент для NVIDIA NIM API.

NVIDIA NIM использует OpenAI-совместимый формат:
  POST https://integrate.api.nvidia.com/v1/chat/completions
  Authorization: Bearer <NVIDIA_API_KEY>
  {"model": "qwen/qwen3-235b-a22b", "messages": [...], "stream": false}

Класс намеренно называется OllamaClient чтобы не трогать
остальной код (generator.py, lesson_service.py и т.д.).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from config import settings

logger = logging.getLogger(__name__)

# NVIDIA NIM endpoint
_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
_CHAT_URL = f"{_NVIDIA_BASE_URL}/chat/completions"
_MODELS_URL = f"{_NVIDIA_BASE_URL}/models"

# Singleton сессия
_session: aiohttp.ClientSession | None = None


def _make_session() -> aiohttp.ClientSession:
    """Создаёт сессию с keep-alive и заголовком авторизации."""
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            limit=4,
            keepalive_timeout=60,
            enable_cleanup_closed=True,
        ),
        timeout=aiohttp.ClientTimeout(
            connect=settings.OLLAMA_CONNECT_TIMEOUT,
            sock_read=settings.OLLAMA_READ_TIMEOUT,
            total=None,
        ),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
        },
    )


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = _make_session()
        logger.debug("Создана новая aiohttp.ClientSession для NVIDIA NIM")
    return _session


async def close_session() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
        logger.info("NVIDIA NIM ClientSession закрыта")


class OllamaClient:
    """
    Клиент NVIDIA NIM — совместимый интерфейс с предыдущей Ollama-версией.
    Внутри использует /v1/chat/completions (OpenAI-формат).
    """

    def __init__(self) -> None:
        self.model = settings.OLLAMA_MODEL          # напр. "qwen/qwen3-235b-a22b"
        self.retries = settings.LLM_RETRIES
        self.retry_delay = settings.LLM_RETRY_DELAY

    def _build_payload(self, prompt: str, system: str) -> dict[str, Any]:
        """
        Строит тело запроса в формате OpenAI chat/completions.

        stream=False — единый JSON-ответ без чанков.
        enable_thinking=False — отключаем цепочку рассуждений Qwen3
                                чтобы получить чистый JSON без <think>...</think>.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        return {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "max_tokens": settings.OLLAMA_NUM_PREDICT,
            "temperature": settings.OLLAMA_TEMPERATURE,
            "top_p": settings.OLLAMA_TOP_P,
            # Отключаем thinking-режим Qwen3 — нам нужен чистый JSON
            "chat_template_kwargs": {"enable_thinking": False},
        }

    async def generate(self, prompt: str, system: str = "") -> str:
        """
        Отправляет запрос к NVIDIA NIM и возвращает текст ответа.

        Retry-логика:
          - HTTP 429 (rate limit) → ждём дольше и повторяем
          - HTTP 5xx → retry после паузы
          - Пустой ответ → retry
        """
        payload = self._build_payload(prompt, system)
        session = await get_session()
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 1):
            t0 = time.perf_counter()
            try:
                async with session.post(_CHAT_URL, json=payload) as resp:
                    elapsed_ms = (time.perf_counter() - t0) * 1000

                    # Rate limit — ждём и повторяем
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", self.retry_delay * attempt * 3))
                        logger.warning(
                            "NVIDIA NIM rate limit (попытка %d/%d) | ждём %.1fс",
                            attempt, self.retries, retry_after,
                        )
                        last_error = RuntimeError("Rate limit 429")
                        await asyncio.sleep(retry_after)
                        continue

                    # Серверные ошибки
                    if resp.status >= 500:
                        body = await resp.text()
                        logger.warning(
                            "NVIDIA NIM HTTP %d (попытка %d/%d) | %.0fмс | %s",
                            resp.status, attempt, self.retries, elapsed_ms, body[:120],
                        )
                        last_error = RuntimeError(f"HTTP {resp.status}: {body[:120]}")
                        await asyncio.sleep(self.retry_delay * attempt)
                        continue

                    # Другие ошибки (401, 404 и т.д.) — не retry, сразу падаем
                    if resp.status not in (200, 201):
                        body = await resp.text()
                        raise RuntimeError(
                            f"NVIDIA NIM HTTP {resp.status}: {body[:300]}"
                        )

                    # Парсим ответ
                    data: dict = await resp.json(content_type=None)

                    # Извлекаем текст из OpenAI-формата
                    choices = data.get("choices", [])
                    if not choices:
                        logger.warning(
                            "NVIDIA NIM: пустой choices (попытка %d/%d)",
                            attempt, self.retries,
                        )
                        last_error = RuntimeError("Пустой choices в ответе")
                        await asyncio.sleep(self.retry_delay * attempt)
                        continue

                    text: str = (
                        choices[0]
                        .get("message", {})
                        .get("content", "")
                        or ""
                    ).strip()

                    if not text:
                        logger.warning(
                            "NVIDIA NIM: пустой content (попытка %d/%d)",
                            attempt, self.retries,
                        )
                        last_error = RuntimeError("Пустой content в ответе")
                        await asyncio.sleep(self.retry_delay * attempt)
                        continue

                    # Логируем usage если есть
                    usage = data.get("usage", {})
                    logger.info(
                        "← NVIDIA NIM OK | попытка=%d | %.0fмс | %d символов | "
                        "tokens: prompt=%s completion=%s",
                        attempt, elapsed_ms, len(text),
                        usage.get("prompt_tokens", "?"),
                        usage.get("completion_tokens", "?"),
                    )
                    return text

            except aiohttp.ClientConnectorError as exc:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                logger.error(
                    "NVIDIA NIM недоступен (попытка %d/%d) | %.0fмс | %s",
                    attempt, self.retries, elapsed_ms, exc,
                )
                last_error = exc
                await asyncio.sleep(self.retry_delay * attempt)

            except asyncio.TimeoutError as exc:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                logger.warning(
                    "NVIDIA NIM timeout (попытка %d/%d) | %.0fмс",
                    attempt, self.retries, elapsed_ms,
                )
                last_error = exc
                await asyncio.sleep(self.retry_delay * attempt)

            except RuntimeError:
                raise  # 401/404 — не retry

            except Exception as exc:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                logger.warning(
                    "NVIDIA NIM ошибка (попытка %d/%d) | %.0fмс | %s: %s",
                    attempt, self.retries, elapsed_ms, type(exc).__name__, exc,
                )
                last_error = exc
                await asyncio.sleep(self.retry_delay * attempt)

        raise RuntimeError(
            f"NVIDIA NIM: все {self.retries} попыток исчерпаны. "
            f"Последняя ошибка: {last_error}"
        )

    async def check_connection(self) -> bool:
        """Проверяет доступность NVIDIA NIM и валидность API ключа."""
        try:
            timeout = aiohttp.ClientTimeout(connect=5.0, sock_read=10.0, total=None)
            headers = {
                "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
                "Content-Type": "application/json",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
                async with s.get(_MODELS_URL) as resp:
                    if resp.status == 401:
                        logger.error("NVIDIA NIM: неверный API ключ (401)")
                        return False
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        models = [m.get("id", "") for m in data.get("data", [])]
                        logger.info(
                            "NVIDIA NIM доступен | моделей: %d | текущая: %s",
                            len(models), self.model,
                        )
                        return True
                    return False
        except Exception as exc:
            logger.warning("NVIDIA NIM недоступен: %s", exc)
            return False

    async def list_models(self) -> list[str]:
        """Возвращает список доступных моделей NVIDIA NIM."""
        try:
            timeout = aiohttp.ClientTimeout(connect=5.0, sock_read=10.0, total=None)
            headers = {"Authorization": f"Bearer {settings.NVIDIA_API_KEY}"}
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
                async with s.get(_MODELS_URL) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json(content_type=None)
                    return [m.get("id", "") for m in data.get("data", [])]
        except Exception:
            return []
