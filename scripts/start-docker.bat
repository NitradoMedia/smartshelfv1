@echo off
setlocal
cd /d "%~dp0.."

where docker >nul 2>&1
if errorlevel 1 (
  echo Docker wurde nicht gefunden. Bitte Docker Desktop installieren und starten.
  pause
  exit /b 1
)

if not exist .env (
  copy .env.example .env >nul
  echo .env angelegt – Passwort/URL bei Bedarf anpassen.
)

if not exist data\pos mkdir data\pos
if not exist data\clips mkdir data\clips
if not exist data\videos mkdir data\videos
if not exist data\recordings mkdir data\recordings
if not exist data\uploads mkdir data\uploads
if not exist data\preview mkdir data\preview
if not exist data\models mkdir data\models

echo.
echo Baue und starte POS Video Guard (Docker)...
echo Dashboard danach: http://localhost:8090
echo (Port 8090 = lokal; 8088 oft Cursor-Cloud-Tunnel)
echo.

docker compose up --build -d
if errorlevel 1 (
  echo.
  echo Start fehlgeschlagen. Laeuft Docker Desktop?
  pause
  exit /b 1
)

echo.
echo Fertig. Oeffne http://localhost:8090
echo Logs: docker compose logs -f
echo Stop:  docker compose down
echo.
start http://localhost:8090
pause
