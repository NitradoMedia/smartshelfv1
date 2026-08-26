@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

echo.
echo === POS Video Guard – lokal OHNE Docker (Windows-Netz) ===
echo Nutzt die Netzwerkkarte des PCs – Kamera 192.168.1.x sollte erreichbar sein.
echo Dashboard: http://localhost:8090
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo Python nicht gefunden. Bitte Python 3.11+ installieren und PATH setzen.
  pause
  exit /b 1
)

REM Docker-Container stoppen, damit Port 8090 frei ist
where docker >nul 2>&1
if not errorlevel 1 (
  docker compose --profile hostnet down >nul 2>&1
  docker compose down >nul 2>&1
)

if not exist .env (
  copy .env.example .env >nul
  echo .env angelegt.
)

if not exist data\pos mkdir data\pos
if not exist data\clips mkdir data\clips
if not exist data\videos mkdir data\videos
if not exist data\recordings mkdir data\recordings
if not exist data\uploads mkdir data\uploads
if not exist data\preview mkdir data\preview
if not exist data\models mkdir data\models
if not exist data\db mkdir data\db

echo Pruefe Kamera-Port 554 von Windows aus...
powershell -NoProfile -Command "try { $r=Test-NetConnection -ComputerName 192.168.1.32 -Port 554 -WarningAction SilentlyContinue; if($r.TcpTestSucceeded){'Kamera TCP OK'} else {'Kamera TCP FEHLER – gleiches WLAN? VPN aus?'} } catch { $_.Exception.Message }"
echo.

echo Installiere/aktualisiere Python-Abhaengigkeiten...
python -m pip install -r backend\requirements.txt
if errorlevel 1 (
  echo pip install fehlgeschlagen.
  pause
  exit /b 1
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo HINWEIS: ffmpeg nicht im PATH – Aufnahme/Clips brauchen ffmpeg.
  echo   winget install ffmpeg
)

set "DATA_DIR=%CD%\data"
set "DEMO_MODE=false"
set "AI_BACKEND=yolo"
set "YOLO_MODEL=%CD%\data\models\yolov8n.pt"
for %%I in ("%CD%\data\db\pos_video_guard.db") do set "DBFILE=%%~fI"
set "DATABASE_URL=sqlite+aiosqlite:///%DBFILE:\=/%"

echo.
echo Starte Server auf http://localhost:8090 ...
echo Stoppen: Strg+C
echo.
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8090
pause
