# 🤖 Telegram Bot for Google Gemini API

Асинхронный Telegram-бот на базе **aiogram 3** и официального **Google GenAI SDK (google-genai)** с поддержкой актуальных моделей Gemini (2.5 Flash, 2.5 Pro, 2.0 Flash), инлайн-режима, rich-форматирования и защиты от спама.

---

## ✨ Возможности

- **Актуальные модели Gemini**: выбор модели на лету через inline-кнопки (/model).
- **Контекст диалога**: память истории переписки для каждого пользователя.
- **Rich-форматирование**: нативная конвертация Markdown в Telegram MessageEntity через 	elegramify-markdown (без битой разметки и без экранирования MarkdownV2).
- **Корректный подсчёт лимитов UTF-16**: безопасная нарезка длинных сообщений под лимит Telegram (4096 code units) с сохранением эмодзи и разметки.
- **Inline Mode**: обращение к боту из любого чата (@bot_username ваш вопрос).
- **Защита от спама и гонок**:
  - Скользящее окно рейт-лимита (в минуту и в сутки).
  - Асинхронные блокировки (syncio.Lock) на пользователя.
- **Контроль доступа**: белый список ALLOWED_USER_IDS через Outer Middleware.
- **Кастомный System Prompt**: настройка характера/инструкций модели через команду /prompt.
- **Готовность к Docker**: запуск от непривилегированного пользователя ot (UID 1000) с ротацией логов.

---

## 🚀 Быстрый старт

### 1. Клонирование и настройка окружения

`ash
cp .env.example .env
`

Заполните переменные в .env:
- TELEGRAM_BOT_TOKEN: токен бота от [@BotFather](https://t.me/BotFather).
- GEMINI_API_KEY: API-ключ от [Google AI Studio](https://aistudio.google.com/).
- ALLOWED_USER_IDS: ваш Telegram ID (узнать можно у [@userinfobot](https://t.me/userinfobot)). Оставьте пустым для публичного режима.

---

### 2. Запуск через Docker Compose (рекомендуется)

`ash
docker compose up -d --build
`

Просмотр логов:
`ash
docker compose logs -f
`

---

### 3. Локальный запуск (без Docker)

Требуется **Python 3.12+**.

`ash
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
python bot.py
`

---

## 🛠 Команды бота

- /start — приветствие, информация о текущей модели и список доступных команд.
- /model — выбор активной модели Gemini (gemini-2.5-flash, gemini-2.5-pro и др.).
- /settings — настройки (переключение Rich-режима, очистка истории диалога).
- /limits — проверка остатка лимитов запросов.
- /prompt — просмотр или изменение системной инструкции (System Prompt).
