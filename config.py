"""
config.py — конфигурация через .env
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ── Telegram ──────────────────────────────────────────────────────────
    BOT_TOKEN: str = Field(..., description="Токен от @BotFather")
    GROUP_CHAT_ID: int = Field(..., description="ID группы (отрицательное число)")

    # ── Расписание ────────────────────────────────────────────────────────
    POST_TIMES: str = Field(default="08:00,19:00")
    LESSON_DAYS: str = Field(default="mon,thu")

    # ── NVIDIA NIM ────────────────────────────────────────────────────────
    NVIDIA_API_KEY: str = Field(
        default="",
        description="API ключ с build.nvidia.com → API Keys",
    )
    # Имя модели в формате NVIDIA NIM: "publisher/model-name"
    OLLAMA_MODEL: str = Field(
        default="qwen/qwen3-235b-a22b",
        description="Модель NVIDIA NIM. Варианты ниже в .env.example",
    )

    # Таймауты (NVIDIA — внешний API, поэтому read timeout больше)
    OLLAMA_CONNECT_TIMEOUT: float = Field(default=10.0)
    OLLAMA_READ_TIMEOUT: float = Field(default=120.0)

    # Параметры генерации
    OLLAMA_TEMPERATURE: float = Field(default=0.7)
    OLLAMA_TOP_P: float = Field(default=0.9)
    OLLAMA_NUM_PREDICT: int = Field(default=1024)

    # Retry
    LLM_RETRIES: int = Field(default=3)
    LLM_RETRY_DELAY: float = Field(default=1.0)

    # ── Совместимость (не используются с NVIDIA NIM, оставлены для check_setup) ──
    OLLAMA_URL: str = Field(default="https://integrate.api.nvidia.com/v1")

    # ── Пути ──────────────────────────────────────────────────────────────
    MATERIALS_DIR: Path = Field(default=Path("materials"))
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///data/pyteacher.db")

    # ── Логика ────────────────────────────────────────────────────────────
    DEFAULT_MODE: str = Field(default="mixed")
    MAX_USED_QUESTIONS: int = Field(default=60)
    FRESH_LESSON_HOURS: int = Field(default=48)

    # ── Свойства ──────────────────────────────────────────────────────────

    @property
    def post_times_parsed(self) -> list[tuple[int, int]]:
        result = []
        for token in self.POST_TIMES.split(","):
            h, m = token.strip().split(":")
            result.append((int(h), int(m)))
        return result

    @property
    def lesson_days_parsed(self) -> list[str]:
        return [d.strip().lower() for d in self.LESSON_DAYS.split(",")]

    # Для совместимости с check_setup и другими модулями
    @property
    def ollama_generate_url(self) -> str:
        return "https://integrate.api.nvidia.com/v1/chat/completions"

    @property
    def ollama_tags_url(self) -> str:
        return "https://integrate.api.nvidia.com/v1/models"


settings = Settings()
