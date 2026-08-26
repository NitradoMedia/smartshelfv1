#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker nicht gefunden. Bitte Docker installieren/starten."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker läuft nicht. Bitte Docker Desktop / Daemon starten."
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env angelegt – Passwort/URL bei Bedarf anpassen."
fi

mkdir -p data/{pos,clips,videos,recordings,uploads,preview,models}

echo "Baue und starte POS Video Guard…"
echo "Dashboard: http://localhost:8090"
echo "(Port 8090 = lokal; 8088 oft Cursor-Cloud-Tunnel)"
docker compose up --build -d

echo
echo "Warte auf Health-Check…"
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${HOST_PORT:-8090}/api/health" >/dev/null 2>&1; then
    echo "OK → http://localhost:${HOST_PORT:-8090}"
    exit 0
  fi
  if ! docker compose ps --status running | grep -q pos-video-guard; then
    echo "Container gestoppt. Logs:"
    docker compose logs --tail 100
    exit 1
  fi
  sleep 3
done

echo "Timeout. Status + Logs:"
docker compose ps
docker compose logs --tail 100
exit 1
