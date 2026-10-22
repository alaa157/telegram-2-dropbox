FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /data \
    && chown bot:bot /data

ENV SESSION_NAME=/data/bot_session
VOLUME ["/data"]
USER bot

CMD ["python", "-m", "app.telegram_bot"]
