@echo off
setlocal
cd /d "%~dp0.."

echo === Kamera vom Windows-Host testen ===
echo.

set CAM=192.168.1.32
set PORT=554
if not "%~1"=="" set CAM=%~1
if not "%~2"=="" set PORT=%~2

echo Ziel: %CAM% Port %PORT%
powershell -NoProfile -Command ^
  "$r = Test-NetConnection -ComputerName '%CAM%' -Port %PORT% -WarningAction SilentlyContinue;" ^
  "Write-Host ('PingSucceeded=' + $r.PingSucceeded);" ^
  "Write-Host ('TcpTestSucceeded=' + $r.TcpTestSucceeded);" ^
  "if ($r.TcpTestSucceeded) { Write-Host 'OK: Windows erreicht die Kamera. Wenn Docker Timeout hat → scripts\run-local.bat' -ForegroundColor Green }" ^
  "else { Write-Host 'FEHLER: Auch Windows erreicht die Kamera nicht. WLAN/VPN/Firewall/IP pruefen.' -ForegroundColor Red }"

echo.
pause
