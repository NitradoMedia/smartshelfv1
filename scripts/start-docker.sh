#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker nicht gefunden. Bitte Docker installieren/starten."
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env angelegt – Passwort/URL bei Bedarf anpassen."
fi

mkdir -p data/{pos,clips,videos,recordings,uploads,preview,models}

echo "Baue und starte POS Video Guard…"
docker compose up --build -d

echo
echo "Fertig → http://localhost:8090"
echo "(Port 8090 = lokal; 8088 ist oft der Cursor-Cloud-Tunnel)"
echo "Logs: docker compose logs -f"
echo "Stop: docker compose down"
