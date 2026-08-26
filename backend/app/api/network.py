"""Network diagnostics: show where the app runs and if RTSP host is reachable."""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/network", tags=["network"])

LOCAL_DASHBOARD = "http://localhost:8090"


class DiagnoseIn(BaseModel):
    url: str = ""


def _tcp_check(host: str, port: int, timeout: float = 3.0) -> dict:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "detail": f"TCP {host}:{port} erreichbar"}
    except OSError as exc:
        return {"ok": False, "detail": str(exc)}


def _local_ips() -> list[str]:
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ip and ip not in ips and ":" not in ip:
                ips.append(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip not in ips:
            ips.insert(0, ip)
    except OSError:
        pass
    return ips


def _env_label(ips: list[str]) -> tuple[bool, str]:
    """Return (camera_lan_unlikely, human label).

    Note: Docker Desktop containers often only see 172.x – that is NOT proof of Cloud.
    Cloud = no 192.168 host IP and (typically) unreachable home cameras.
    """
    if os.getenv("FORCE_LAN_OK", "").lower() in ("1", "true", "yes"):
        return False, "lokal (FORCE_LAN_OK)"
    if any(ip.startswith("192.168.") for ip in ips):
        return False, "lokales Netz (192.168.x)"
    if any(ip.startswith("10.") for ip in ips):
        return False, "privates Netz (10.x)"
    # 172.16–31 are private; Docker + many clouds use them
    return True, "kein Heimnetz-IP (Docker-Cloud oder Container-Bridge)"


@router.get("/info")
async def network_info():
    ips = _local_ips()
    unlikely, label = _env_label(ips)
    return {
        "hostname": socket.gethostname(),
        "local_ips": ips,
        "looks_like_cloud": unlikely,
        "env_label": label,
        "dashboard_url": LOCAL_DASHBOARD,
        "hint": (
            "Die Kamera unter 192.168.x ist nur erreichbar, wenn die App auf deinem "
            f"PC im gleichen WLAN/LAN läuft. Lokal: scripts\\start-docker.bat → {LOCAL_DASHBOARD}\n"
            "Wenn die Adresse im Browser localhost ist, aber diese Diagnose weiterhin "
            "Timeout zeigt, öffnest du noch den Cursor-Cloud-Tunnel – Tab schließen und "
            f"nach lokalem Docker-Start {LOCAL_DASHBOARD} neu öffnen."
            if unlikely
            else "App scheint im privaten Netz zu laufen – RTSP sollte erreichbar sein."
        ),
        "private_ips": [
            ip for ip in ips if ip.startswith(("192.168.", "10.", "172."))
        ],
    }


@router.post("/diagnose")
async def diagnose(body: DiagnoseIn):
    info = await network_info()
    url = (body.url or "").strip()
    result = {
        **info,
        "url": url,
        "rtsp_host": None,
        "rtsp_port": None,
        "tcp": None,
        "recommendation": info["hint"],
        "needs_local_restart": False,
    }
    if not url:
        return result
    parsed = urlparse(url if "://" in url else f"rtsp://{url}")
    host = parsed.hostname
    port = parsed.port or 554
    result["rtsp_host"] = host
    result["rtsp_port"] = port
    if not host:
        result["tcp"] = {"ok": False, "detail": "Kein Host in der URL"}
        return result
    tcp = _tcp_check(host, port)
    result["tcp"] = tcp
    lan_cam = host.startswith("192.168.") or host.startswith("10.")
    if tcp["ok"]:
        result["looks_like_cloud"] = False
        result["env_label"] = "Kamera erreichbar"
        result["recommendation"] = (
            f"TCP zu {host}:{port} OK. Wenn Preview trotzdem scheitert: "
            "User/Passwort und RTSP-Pfad prüfen "
            "(Reolink z.B. /h264Preview_01_main, /Preview_01_main oder /11)."
        )
        return result

    if lan_cam:
        result["needs_local_restart"] = True
        result["recommendation"] = (
            f"Port {port} auf {host} ist von DIESEM Prozess aus nicht erreichbar "
            f"(Server-IPs: {', '.join(info['local_ips']) or 'unbekannt'}).\n\n"
            "Du siehst das Dashboard oft als localhost – aber die Kamera-Verbindung "
            "kommt vom Server, der die App ausführt. Cursor-Cloud hat kein Heimnetz.\n\n"
            "Auf dem Windows-PC (Docker Desktop):\n"
            "  1. Branch: git checkout cursor/pos-video-guard-6e31 && git pull\n"
            "  2. scripts\\start-docker.bat\n"
            f"  3. Browser NEU öffnen: {LOCAL_DASHBOARD}\n"
            "  4. Diagnose muss „Kamera … → OK“ zeigen (nicht Timeout).\n\n"
            "Nicht 8088 verwenden, wenn dort noch der Cloud-Tunnel hängt."
        )
    else:
        result["recommendation"] = (
            f"TCP zu {host}:{port} fehlgeschlagen: {tcp['detail']}. "
            "Prüfe IP, RTSP-Port (meist 554), Kamera-Online-Status und Firewall. "
            "Reolink-URL oft: rtsp://USER:PASS@IP:554/h264Preview_01_main "
            f"(aktuell: Pfad '{parsed.path or '/'}')."
        )
    return result
