FROM python:3.12-slim

# логи сразу в docker logs, без буферизации; не плодим .pyc в образе
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Устанавливаем gosu и ffmpeg для безопасного сброса root-прав и кодирования аудио OGG Opus
RUN apt-get update && apt-get install -y --no-install-recommends gosu ffmpeg && rm -rf /var/lib/apt/lists/*

# сначала только requirements — чтобы пересборка кода не дёргала pip install каждый раз
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# создаём пользователя bot и настраиваем entrypoint
RUN mkdir -p /app/data && useradd --create-home --uid 1000 bot && chown -R bot:bot /app && chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "bot.py"]

