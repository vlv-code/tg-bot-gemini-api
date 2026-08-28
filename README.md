# 🤖 Telegram Bot for Google Gemini API

Асинхронный Telegram-бот на базе **aiogram 3** и официального **Google GenAI SDK (`google-genai`)** с поддержкой актуальных моделей Gemini (3.5 Flash Lite, 3.1 Flash Lite, 3.7 Flash, 3.5 Flash, 2.5 Flash), мультимодальности (фото, PDF, голосовые), генерации речи (TTS), постоянного хранилища SQLite (`aiosqlite`) и защиты от спама.

---

## ✨ Возможности

- **Актуальные модели Gemini**: выбор модели на лету через inline-кнопки (`/model`).
- **Мультимодальность**:
  - 🖼 **Фото / Изображения**: отправьте фото с вопросом (или без) — Gemini распознает и проанализирует его.
  - 📄 **Документы (PDF / текст)**: отправьте файл для анализа, перевода или резюме.
  - 🎙 **Голосовые сообщения**: бот слушает голосовые сообщения в Telegram и может отвечать на них голосом.
- **Синтез речи (TTS & Voice Replies)**:
  - Прямая озвучка текста командой `/tts <текст>`.
  - Режим голосовых ответов («🎙 Голосовые ответы» в `/settings` или автоматический ответ на войс).
- **Персистентное хранилище (SQLite + `aiosqlite`)**:
  - Сохранение настроек (модель, rich-режим, голосовой режим, system prompt) и истории диалогов в `data/bot.db`.
  - Данные не теряются при перезапусках контейнера благодаря монтированию volume.
- **Rich-форматирование**:
  - Конвертация Markdown в Telegram `MessageEntity` через `telegramify-markdown` (без битой разметки и без проблем с экранированием).
  - Корректный подсчёт UTF-16 code units для безопасного разбиения длинных сообщений.
- **Индивидуальный System Prompt**:
  - Настройка персонального характера и роли модели для каждого пользователя через `/prompt`.
- **Inline Mode**:
  - Обращение к боту из любого чата (`@bot_username ваш вопрос`).
- **Защита от спама и гонок**:
  - Скользящее окно рейт-лимита (в минуту и в сутки).
  - Асинхронные блокировки (`asyncio.Lock`) на пользователя.
- **Контроль доступа**:
  - Белый список `ALLOWED_USER_IDS` через Outer Middleware.
- **Готовность к Docker**:
  - Запуск от непривилегированного пользователя `bot` (UID 1000) с ротацией логов.

---

## 🚀 Быстрый старт

### 1. Клонирование и настройка окружения

```bash
git clone https://github.com/vlv-code/tg-bot-gemini-api.git
cd tg-bot-gemini-api
cp .env.example .env
```

Заполните переменные в .env:
- TELEGRAM_BOT_TOKEN: токен бота от [@BotFather](https://t.me/BotFather).
- GEMINI_API_KEY: API-ключ от [Google AI Studio](https://aistudio.google.com/).
- ALLOWED_USER_IDS: ваш Telegram ID (узнать можно у [@userinfobot](https://t.me/userinfobot)). Оставьте пустым для публичного режима.

---

### 2. Запуск через Docker Compose (рекомендуется)

```bash
docker compose up -d --build
```

Просмотр логов:
```bash
docker compose logs -f
```

---

### 3. Локальный запуск (без Docker)

Требуется **Python 3.12+**.

```bash
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
python bot.py
```

---

## 🛠 Команды бота

- /start — приветствие, информация о текущей модели и список доступных команд.
- /model — выбор активной модели Gemini (`gemini-3.5-flash-lite`, `gemini-3.7-flash` и др.).
- /settings — настройки (Rich-режим, голосовые ответы, очистка истории диалога).
- /prompt — просмотр, изменение или сброс индивидуального системного промпта.
- /tts <текст> — озвучить текст голосовым сообщением.
- /limits — проверка остатка лимитов запросов.
