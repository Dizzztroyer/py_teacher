"""
storage/db.py — движок SQLAlchemy, фабрика сессий, инициализация схемы.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from storage.models import Base

logger = logging.getLogger(__name__)

# Создаём директорию для БД, если её нет
Path("data").mkdir(parents=True, exist_ok=True)

_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

_SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_engine,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Создаёт таблицы (если не существуют)."""
    async with _engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, checkfirst=True))
    logger.info("✅ БД инициализирована: %s", settings.DATABASE_URL)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager для сессии."""
    async with _SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
