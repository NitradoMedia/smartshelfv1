#!/bin/sh
set -e

# Als root: Datenverzeichnisse anlegen und Rechte für appuser setzen
# (Windows-Bind-Mounts sind sonst oft nicht beschreibbar → Container "Up", App crasht)
mkdir -p /data/pos /data/clips /data/uploads /data/videos \
  /data/recordings /data/preview /data/models /data/db

if [ "$(id -u)" = "0" ]; then
  chown -R appuser:appuser /data 2>/dev/null || true
fi

# YOLO-Gewichte vom Image in gemountetes Volume kopieren
if [ ! -f /data/models/yolov8n.pt ]; then
  if [ -f /opt/yolov8n.pt ]; then
    cp /opt/yolov8n.pt /data/models/yolov8n.pt
  elif [ -f /app/yolov8n.pt ]; then
    cp /app/yolov8n.pt /data/models/yolov8n.pt
  fi
  if [ "$(id -u)" = "0" ] && [ -f /data/models/yolov8n.pt ]; then
    chown appuser:appuser /data/models/yolov8n.pt 2>/dev/null || true
  fi
fi

# Drop privileges wenn als root gestartet (Fallback root = OK für lokales Docker Desktop)
if [ "$(id -u)" = "0" ] && command -v runuser >/dev/null 2>&1; then
  exec runuser -u appuser -- "$@"
fi

exec "$@"
