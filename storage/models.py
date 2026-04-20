"""
storage/models.py — SQLAlchemy ORM-модели (SQLite).

Таблицы:
  lessons           — PDF-уроки
  generated_content — основная таблица истории с hash-дедупликацией  ← NEW
  generated_items   — расширенный контент (theory, mode, sent)
  used_questions    — flat-список вопросов для LLM-контекста
  polls             — Telegram-опросы
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ═══════════════════════════════════════════════════════════════════════════
#  Lesson
# ═══════════════════════════════════════════════════════════════════════════

class Lesson(Base):
    """PDF-урок, обработанный ingestor'ом."""
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_file: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now()
    )
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    topics: Mapped[str] = mapped_column(Text, nullable=False, default="")    # JSON list
    keywords: Mapped[str] = mapped_column(Text, nullable=False, default="")  # JSON list
    page_count: Mapped[int] = mapped_column(Integer, default=0)

    generated_contents: Mapped[list["GeneratedContent"]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )
    generated_items: Mapped[list["GeneratedItem"]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  GeneratedContent  ← ОСНОВНАЯ ТАБЛИЦА ИСТОРИИ
# ═══════════════════════════════════════════════════════════════════════════

class GeneratedContent(Base):
    """
    Полная запись каждого сгенерированного блока контента.

    Является единственным источником правды для:
      - дедупликации (поле hash, индексированное)
      - человекочитаемой истории (экспортируется в history.md)
      - аналитики и отладки

    Поле hash = SHA-256(normalize(question)) — быстрый поиск дублей за O(1).
    Индекс по hash: уникальный → INSERT CONFLICT = дубль обнаружен.
    """
    __tablename__ = "generated_content"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Временна́я метка создания (UTC, не изменяется)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False
    )

    # Связь с уроком (nullable — если контент из встроенных тем)
    lesson_id: Mapped[int | None] = mapped_column(
        ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True
    )
    source_name: Mapped[str] = mapped_column(
        String(512), nullable=False, default="",
        doc="Имя урока/темы — для человекочитаемой истории"
    )

    # Основной контент
    question: Mapped[str] = mapped_column(
        Text, nullable=False,
        doc="Первый/главный вопрос блока — используется для хеширования"
    )
    poll_question: Mapped[str] = mapped_column(String(300), nullable=False)
    options_json: Mapped[str] = mapped_column(
        Text, nullable=False,
        doc='JSON-массив строк: ["A", "B", "C", "D"]'
    )
    correct_option: Mapped[int] = mapped_column(
        Integer, nullable=False,
        doc="0-based индекс правильного варианта"
    )
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")

    # Хеш для дедупликации — SHA-256(normalize(question))
    # Уникальный индекс гарантирует: два одинаковых вопроса не попадут в БД.
    hash: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="SHA-256 hex от нормализованного вопроса"
    )

    # Флаг "записан в history.md"
    written_to_md: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Constraints & Indexes
    __table_args__ = (
        UniqueConstraint("hash", name="uq_generated_content_hash"),
        Index("ix_generated_content_created_at", "created_at"),
        Index("ix_generated_content_lesson_id", "lesson_id"),
    )

    lesson: Mapped["Lesson | None"] = relationship(back_populates="generated_contents")


# ═══════════════════════════════════════════════════════════════════════════
#  GeneratedItem  (расширенный контент: theory, mode, sent-статус)
# ═══════════════════════════════════════════════════════════════════════════

class GeneratedItem(Base):
    """Сгенерированный пост целиком (theory + questions + poll)."""
    __tablename__ = "generated_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lesson_id: Mapped[int | None] = mapped_column(
        ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True
    )
    # Связь с GeneratedContent (один к одному, опционально)
    content_id: Mapped[int | None] = mapped_column(
        ForeignKey("generated_content.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now()
    )
    mode: Mapped[str] = mapped_column(String(32), default="mixed")
    theory: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    lesson: Mapped["Lesson | None"] = relationship(back_populates="generated_items")
    questions: Mapped[list["UsedQuestion"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    poll: Mapped["Poll | None"] = relationship(
        back_populates="item", cascade="all, delete-orphan", uselist=False
    )


# ═══════════════════════════════════════════════════════════════════════════
#  UsedQuestion  (flat-список для LLM-контекста)
# ═══════════════════════════════════════════════════════════════════════════

class UsedQuestion(Base):
    """Плоский список всех отправленных вопросов (для передачи в LLM-промпт)."""
    __tablename__ = "used_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("generated_items.id", ondelete="SET NULL"), nullable=True
    )
    lesson_id: Mapped[int | None] = mapped_column(
        ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_short: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now()
    )

    item: Mapped["GeneratedItem | None"] = relationship(back_populates="questions")


# ═══════════════════════════════════════════════════════════════════════════
#  Poll
# ═══════════════════════════════════════════════════════════════════════════

class Poll(Base):
    """Telegram-опрос."""
    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("generated_items.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    question: Mapped[str] = mapped_column(String(300), nullable=False)
    options: Mapped[str] = mapped_column(Text, nullable=False)
    correct_option_id: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    telegram_poll_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now()
    )

    item: Mapped["GeneratedItem"] = relationship(back_populates="poll")
