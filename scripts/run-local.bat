@echo off
REM Start POS Video Guard locally (same LAN as camera)
cd /d "%~dp0.."
if not exist .env copy .env.example .env
echo Starting on http://localhost:8088 ...
echo Camera RTSP example: rtsp://admin:PASS@192.168.1.32:554/h264Preview_01_main
set DATA_DIR=%CD%\data
set DEMO_MODE=true
set AI_BACKEND=yolo
set YOLO_MODEL=%CD%\data\models\yolov8n.pt
if not exist "%DATA_DIR%\models" mkdir "%DATA_DIR%\models"
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8088
pause
