#!/bin/sh
set -e
mkdir -p /data/pos /data/clips /data/uploads /data/videos /data/recordings /data/preview /data/models /data/db

# Seed YOLO weights into mounted volume if empty
if [ ! -f /data/models/yolov8n.pt ] && [ -f /app/yolov8n.pt ]; then
  cp /app/yolov8n.pt /data/models/yolov8n.pt
elif [ ! -f /data/models/yolov8n.pt ] && [ -f /data/models/../yolov8n.pt ]; then
  true
fi
# Image build copies to /data/models before USER switch; volume may overlay —
# keep a copy under /app for seeding:
if [ ! -f /data/models/yolov8n.pt ] && [ -f /opt/yolov8n.pt ]; then
  cp /opt/yolov8n.pt /data/models/yolov8n.pt
fi

exec "$@"
