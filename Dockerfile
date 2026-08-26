FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEMO_MODE=false \
    AI_BACKEND=yolo \
    YOLO_MODEL=/data/models/yolov8n.pt \
    DATA_DIR=/data \
    DATABASE_URL=sqlite+aiosqlite:////data/db/pos_video_guard.db

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt \
    && python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" \
    && mkdir -p /opt \
    && cp yolov8n.pt /opt/yolov8n.pt

COPY backend /app/backend
COPY frontend /app/frontend
COPY demo /app/demo
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && mkdir -p /data/pos /data/clips /data/uploads /data/videos /data/recordings /data/preview /data/models /data/db \
    && useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app /data /opt

# Entrypoint läuft als root (chown der Windows-Volumes), startet dann appuser
USER root
WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
