@echo off
setlocal
cd /d "%~dp0.."

echo === Docker Desktop ===
docker info 2>&1 | findstr /I /C:"Server Version" /C:"Operating System" /C:"ERROR" /C:"error"
echo.

echo === Compose Status ===
docker compose ps -a
echo.

echo === Health (Host Port 8090) ===
curl -fsS http://127.0.0.1:8090/api/health
echo.
echo.

echo === Network Diagnose (im Container) ===
curl -fsS http://127.0.0.1:8090/api/network/diagnose 2>nul
echo.
echo.

echo === Letzte Container-Logs ===
docker compose logs --tail 120
echo.

echo === Ports ===
docker compose port pos-video-guard 8000 2>nul
echo.
echo Wenn Health fehlschlaegt: docker compose down ^&^& scripts\start-docker.bat
echo URL lokal: http://localhost:8090  (nicht 8088)
pause
