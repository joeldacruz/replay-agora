# Imagem única para API e worker (comando diferente no compose). Somente desenvolvimento/homologação local.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/replay
COPY services/api/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt
COPY services ./services
COPY apps ./apps
COPY packages ./packages
ENV PYTHONPATH=/srv/replay/services/api:/srv/replay/services/media-worker FRONTEND_DIR=/srv/replay STORAGE_DIR=/data/storage \
    DATABASE_URL=sqlite:////data/replay.db PYTHONUNBUFFERED=1
RUN useradd -r -u 10001 replay && mkdir -p /data && chown replay /data
USER replay
VOLUME /data
WORKDIR /srv/replay/services/api
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
