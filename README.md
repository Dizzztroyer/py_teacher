# 🐍 Python Teacher Bot

> Telegram-бот для автоматического обучения Python в группе — генерирует вопросы, опросы и объяснения на основе материалов ваших уроков.

---

## Что умеет

- 📄 **Читает PDF-материалы уроков** и генерирует вопросы именно по ним
- 🤖 **Генерирует уникальный контент** через LLM — теорию, вопросы для размышления и Telegram-квизы
- 🔁 **Не повторяет вопросы** — SHA-256 дедупликация через SQLite
- 📅 **Постит по расписанию** — 2 раза в день, автоматически
- 📝 **Ведёт историю** — каждый вопрос сохраняется в БД и `history.md`
- 🎯 **Несколько режимов** — по последнему уроку, по архивным, смешанный
- ⚡ **Быстро** — асинхронный стек, aiohttp с keep-alive

---

## Стек

| Компонент | Технология |
|-----------|-----------|
| Бот | [aiogram 3](https://docs.aiogram.dev/) |
| LLM | [NVIDIA NIM API](https://build.nvidia.com) (Llama 4 / Qwen3) |
| Планировщик | [APScheduler](https://apscheduler.readthedocs.io/) |
| БД | SQLite + [SQLAlchemy 2](https://docs.sqlalchemy.org/) async |
| PDF | [pypdf](https://pypdf.readthedocs.io/) |
| HTTP | [aiohttp](https://docs.aiohttp.org/) |
| Конфиг | [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) |

---

## Архитектура

```
pyteacher/
├── main.py                  # Точка входа
├── config.py                # Настройки через .env
│
├── bot/
│   └── handlers.py          # Команды Telegram
│
├── llm/
│   ├── ollama_client.py     # HTTP-клиент NVIDIA NIM
│   └── generator.py         # Построение промптов, парсинг JSON
│
├── logic/
│   ├── lesson_service.py    # Оркестратор: режимы, дедуп, отправка
│   └── topics.py            # Встроенный банк тем (fallback без PDF)
│
├── content/
│   ├── pdf_reader.py        # Чтение и очистка PDF
│   └── lesson_ingestor.py   # Pipeline: PDF → summary → БД
│
├── scheduler/
│   └── tasks.py             # APScheduler, 2 поста в день
│
└── storage/
    ├── models.py            # SQLAlchemy ORM (5 таблиц)
    ├── db.py                # Движок, фабрика сессий
    ├── repository.py        # Репозитории
    ├── content_repo.py      # Репозиторий истории + дедуп
    ├── hashing.py           # SHA-256, simhash
    └── history_writer.py    # Append-only запись history.md
```

---

## Быстрый старт

### 1. Клонируй репозиторий

```bash
git clone https://github.com/твой-username/pyteacher.git
cd pyteacher
```

### 2. Создай виртуальное окружение

```bash
# Python 3.11 или 3.12 (не 3.14!)
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Настрой `.env`

```bash
cp .env.example .env
```

Открой `.env` и заполни:

```env
# Telegram — токен от @BotFather
BOT_TOKEN=1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ID чата (личный ID или отрицательный ID группы)
GROUP_CHAT_ID=4*******9

# NVIDIA NIM — ключ с build.nvidia.com → API Keys
NVIDIA_API_KEY=nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Модель (рекомендуется)
OLLAMA_MODEL=meta/llama-4-maverick-17b-128e-instruct
```

### 4. Добавь PDF-уроки

```bash
cp /path/to/your/lesson.pdf materials/
```

### 5. Проверь конфигурацию

```bash
python check_setup.py
```

Все пункты должны быть зелёными ✅

### 6. Запускай

```bash
python main.py
```

---

## Команды бота

| Команда | Описание | Доступ |
|---------|----------|--------|
| `/question` | Вопрос в режиме по умолчанию | все |
| `/question latest` | По последнему PDF-уроку | все |
| `/question review` | По архивным урокам | все |
| `/question mixed` | Смешанный режим | все |
| `/question topic:LEGB` | По конкретной теме | все |
| `/ingest_latest` | Обработать новый PDF | все |
| `/lessons` | Список загруженных уроков | все |
| `/lesson_status` | Детали последнего урока | все |
| `/regenerate` | Перегенерировать вопрос | все |
| `/history` | История вопросов | все |
| `/topics` | Встроенные темы | все |
| `/status` | Статус бота | все |

---

## Режимы генерации

| Режим | Логика |
|-------|--------|
| `latest` | Вопросы строго по последнему загруженному PDF |
| `review` | Случайный архивный урок |
| `mixed` | Свежий урок если есть, иначе чередует архивные |
| `topic:<name>` | Из встроенного банка тем (без PDF) |

**Автоматическое переключение по расписанию:**
- В дни урока (`mon`, `thu`) → режим `latest`
- В остальные дни → режим `mixed`

---

## Как работает дедупликация

```
Новый вопрос
     ↓
normalize() → lowercase, убрать пунктуацию
     ↓
SHA-256(normalized_text) → 64-символьный hash
     ↓
SELECT по hash в SQLite (O(1), уникальный индекс)
     ↓
Дубль? → регенерировать (до 3 попыток)
Новый? → сохранить в generated_content + записать в history.md
```

---

## История вопросов

Каждый сгенерированный вопрос сохраняется в двух местах:

**SQLite** — для дедупликации и аналитики:
```
data/pyteacher.db
  └── generated_content (id, hash, question, poll_question, options_json, ...)
```

**Markdown** — для чтения людьми (только append, никогда не перезаписывается):
```
data/history/
  ├── history_2026_04.md
  └── history_2026_05.md
```

Пример записи:
```markdown
## 2026-04-20 14:35:22 UTC | ID: 42 | Источник: Lesson10.pdf | medium

**Вопрос**
Чем отличается *args от **kwargs?

**Опрос**
Что принимает *args?

**Варианты**
- A: Позиционные аргументы как tuple  ✅
- B: Именованные аргументы как dict
- C: Любые аргументы как list
- D: Только числовые аргументы

**Правильный ответ**
0 → A: Позиционные аргументы как tuple

**Объяснение**
*args собирает все позиционные аргументы в tuple.
```

---

## Расписание

Настраивается через `.env`:

```env
POST_TIMES=08:00,19:00   # UTC
LESSON_DAYS=mon,thu      # Дни когда проходят уроки
```

Бот дополнительно запускает автоинжест новых PDF за 2 минуты до каждого поста в дни урока.

---

## Встроенные темы (без PDF)

Если PDF-уроков нет — бот использует встроенный банк из 10 тем:

- Типы данных Python
- list, tuple, dict, set
- Функции: `*args` и `**kwargs`
- LEGB — правило областей видимости
- Генераторы и `yield`
- Замыкания (closures)
- Декораторы

---

## Переменные окружения

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `BOT_TOKEN` | — | Токен Telegram-бота |
| `GROUP_CHAT_ID` | — | ID чата для постинга |
| `NVIDIA_API_KEY` | — | Ключ NVIDIA NIM |
| `OLLAMA_MODEL` | `meta/llama-4-maverick-17b-128e-instruct` | Модель LLM |
| `POST_TIMES` | `08:00,19:00` | Время постинга (UTC) |
| `LESSON_DAYS` | `mon,thu` | Дни уроков |
| `OLLAMA_CONNECT_TIMEOUT` | `10.0` | Таймаут подключения (сек) |
| `OLLAMA_READ_TIMEOUT` | `120.0` | Таймаут чтения (сек) |
| `OLLAMA_TEMPERATURE` | `0.7` | Температура генерации |
| `OLLAMA_NUM_PREDICT` | `1024` | Макс. токенов ответа |
| `LLM_RETRIES` | `3` | Попыток при ошибке |
| `LLM_RETRY_DELAY` | `1.0` | Пауза между попытками (сек) |
| `MATERIALS_DIR` | `materials` | Папка с PDF |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/pyteacher.db` | URL базы данных |
| `MAX_USED_QUESTIONS` | `60` | История для дедупликации |
| `FRESH_LESSON_HOURS` | `48` | Часов "свежести" урока |

---

## Автозапуск (Linux)

```bash
sudo nano /etc/systemd/system/pyteacher.service
```

```ini
[Unit]
Description=Python Teacher Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/pyteacher
ExecStart=/path/to/pyteacher/.venv/bin/python main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable pyteacher
sudo systemctl start pyteacher
```

---

## Требования

- Python **3.11** или **3.12** (не 3.13+)
- Аккаунт на [build.nvidia.com](https://build.nvidia.com) (бесплатно, до 40 rpm)
- Telegram-бот от [@BotFather](https://t.me/BotFather)

---

## Лицензия

MIT
