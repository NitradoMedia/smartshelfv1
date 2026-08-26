#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
set -a
# shellcheck disable=SC1091
[ -f .env ] && . ./.env
set +a
export DATA_DIR="${DATA_DIR:-$ROOT/data}"
export DEMO_MODE="${DEMO_MODE:-true}"
export AI_BACKEND="${AI_BACKEND:-yolo}"
export YOLO_MODEL="${YOLO_MODEL:-$DATA_DIR/models/yolov8n.pt}"
export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///$DATA_DIR/pos_video_guard.db}"
# ensure weights available
if [ ! -f "$YOLO_MODEL" ] && [ -f "$ROOT/yolov8n.pt" ]; then
  mkdir -p "$(dirname "$YOLO_MODEL")"
  cp "$ROOT/yolov8n.pt" "$YOLO_MODEL"
fi
mkdir -p "$DATA_DIR"/{pos,clips,uploads}
cd "$ROOT/backend"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8088
