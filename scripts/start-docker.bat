@echo off
setlocal
cd /d "%~dp0.."

where docker >nul 2>&1
if errorlevel 1 (
  echo Docker wurde nicht gefunden. Bitte Docker Desktop installieren und starten.
  pause
  exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop laeuft nicht. Bitte starten und erneut versuchen.
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
echo Dashboard: http://localhost:8090
echo.
echo Wenn die Kamera Timeout hat: lieber scripts\run-local.bat
echo   oder: set DOCKER_HOST_NETWORK=1 ^(Host-Networking in Docker Desktop an^)
echo.

REM Alten Container stoppen, damit Name/Ports frei sind
docker compose --profile hostnet down >nul 2>&1
docker compose down >nul 2>&1

if /I "%DOCKER_HOST_NETWORK%"=="1" (
  echo Modus: Host-Networking ^(Profil hostnet^)
  docker compose --profile hostnet up --build -d pos-video-guard-host
) else (
  docker compose up --build -d pos-video-guard
)
if errorlevel 1 (
  echo.
  echo Start fehlgeschlagen.
  docker compose logs --tail 80
  pause
  exit /b 1
)

echo.
echo Warte auf Health-Check (max. ~90s)...
set /a tries=0
:wait_health
set /a tries+=1
curl -fsS http://127.0.0.1:8090/api/health >nul 2>&1
if not errorlevel 1 goto healthy
docker compose ps --status running 2>nul | findstr /I pos-video-guard >nul 2>&1
if errorlevel 1 (
  echo.
  echo Container ist gestoppt/abgestürzt. Letzte Logs:
  docker compose logs --tail 100
  echo.
  echo Tipp: scripts\run-local.bat ^(ohne Docker, Windows-Netz^)
  pause
  exit /b 1
)
if %tries% GEQ 30 (
  echo.
  echo Timeout: App antwortet nicht auf http://localhost:8090
  docker compose ps
  docker compose logs --tail 100
  pause
  exit /b 1
)
timeout /t 3 /nobreak >nul
goto wait_health

:healthy
echo.
echo OK – Dashboard: http://localhost:8090
echo Kamera-Test vom Host: scripts\test-camera.bat
echo Bei Kamera-Timeout im Container: scripts\run-local.bat
echo.
start http://localhost:8090
pause
