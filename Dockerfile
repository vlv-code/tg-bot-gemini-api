FROM python:3.12-slim

# логи сразу в docker logs, без буферизации; не плодим .pyc в образе
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# сначала только requirements — чтобы пересборка кода не дёргала pip install каждый раз
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# без рута
RUN mkdir -p /app/data && useradd --create-home --uid 1000 bot && chown -R bot:bot /app
USER bot

# бот сам логирует старт/ошибки, healthcheck поверх процесса
# сознательно не добавлен — polling ничего не слушает, восстановление
# после падения делает restart-policy в compose.
CMD ["python", "bot.py"]
