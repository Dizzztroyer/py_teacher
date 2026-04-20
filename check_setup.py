"""
check_setup.py — проверка конфигурации перед запуском.
Запуск: python check_setup.py
"""
from __future__ import annotations

import asyncio
import sys


async def main() -> None:
    ok = True
    print("=" * 58)
    print("  Python Teacher Bot — проверка конфигурации")
    print("=" * 58)

    # ── 1. Config ──────────────────────────────────────────────
    print("\n[1] Config / .env")
    try:
        from config import settings
        print("  ✅ .env загружен")
        print(f"     BOT_TOKEN      : ***{settings.BOT_TOKEN[-6:]}")
        print(f"     GROUP_CHAT_ID  : {settings.GROUP_CHAT_ID}")
        print(f"     POST_TIMES     : {settings.POST_TIMES}")
        print(f"     LESSON_DAYS    : {settings.LESSON_DAYS}")
        print(f"     NVIDIA_API_KEY : ***{settings.NVIDIA_API_KEY[-6:] if settings.NVIDIA_API_KEY else 'НЕ ЗАДАН!'}")
        print(f"     OLLAMA_MODEL   : {settings.OLLAMA_MODEL}")
        print(f"     READ_TIMEOUT   : {settings.OLLAMA_READ_TIMEOUT}с")
        if not settings.NVIDIA_API_KEY:
            print("  ❌ NVIDIA_API_KEY не задан в .env!")
            ok = False
    except Exception as exc:
        print(f"  ❌ Ошибка: {exc}")
        ok = False

    # ── 2. Telegram ────────────────────────────────────────────
    print("\n[2] Telegram Bot API")
    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from config import settings
        bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        me = await bot.get_me()
        print(f"  ✅ Бот: @{me.username} (id={me.id})")
        await bot.session.close()
    except Exception as exc:
        print(f"  ❌ Telegram: {exc}")
        ok = False

    # ── 3. NVIDIA NIM ──────────────────────────────────────────
    print("\n[3] NVIDIA NIM API")
    try:
        from llm.ollama_client import OllamaClient, close_session
        from config import settings
        client = OllamaClient()
        alive = await client.check_connection()
        if alive:
            print(f"  ✅ NVIDIA NIM доступен")
            print(f"     Модель: {settings.OLLAMA_MODEL}")
        else:
            print("  ❌ NVIDIA NIM недоступен — проверь NVIDIA_API_KEY")
            ok = False
    except Exception as exc:
        print(f"  ❌ NVIDIA NIM: {exc}")
        ok = False

    # ── 4. LLM smoke test ──────────────────────────────────────
    print("\n[4] LLM smoke test")
    try:
        from llm.ollama_client import OllamaClient, close_session
        import time
        client = OllamaClient()
        t0 = time.perf_counter()
        resp = await client.generate(
            prompt="Ответь одним словом на русском: столица России?",
            system="Отвечай максимально кратко.",
        )
        elapsed = (time.perf_counter() - t0) * 1000
        if resp:
            print(f"  ✅ LLM ответила за {elapsed:.0f}мс: {resp[:80]!r}")
        else:
            print(f"  ⚠️  Пустой ответ за {elapsed:.0f}мс")
            ok = False
        await close_session()
    except Exception as exc:
        print(f"  ❌ LLM smoke test: {exc}")
        ok = False

    # ── 5. База данных ─────────────────────────────────────────
    print("\n[5] SQLite база данных")
    try:
        from storage.db import init_db
        await init_db()
        print("  ✅ БД инициализирована")
    except Exception as exc:
        print(f"  ❌ БД: {exc}")
        ok = False

    # ── 6. PDF-папка ───────────────────────────────────────────
    print("\n[6] Папка materials/")
    try:
        from config import settings
        from content.lesson_ingestor import LessonIngestor
        ingestor = LessonIngestor()
        pdfs = ingestor.list_pdf_files()
        if pdfs:
            print(f"  ✅ Найдено {len(pdfs)} PDF:")
            for p in pdfs[:5]:
                print(f"     • {p.name} ({p.stat().st_size // 1024} КБ)")
        else:
            print(f"  ℹ️  В {settings.MATERIALS_DIR}/ нет PDF (нормально при первом запуске)")
    except Exception as exc:
        print(f"  ❌ materials/: {exc}")

    # ── Итог ───────────────────────────────────────────────────
    print("\n" + "=" * 58)
    if ok:
        print("✅ Всё готово! Запускай: python main.py")
    else:
        print("❌ Есть проблемы — исправь их перед запуском")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
