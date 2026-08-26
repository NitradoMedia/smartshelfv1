"""Network diagnostics: show where the app runs and if RTSP host is reachable."""

from __future__ import annotations

import socket
from urllib.parse import urlparse

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/network", tags=["network"])


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
    # also try UDP trick for primary outbound interface
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


@router.get("/info")
async def network_info():
    ips = _local_ips()
    private = [ip for ip in ips if ip.startswith(("192.168.", "10.", "172."))]
    return {
        "hostname": socket.gethostname(),
        "local_ips": ips,
        "looks_like_cloud": not any(ip.startswith("192.168.") for ip in ips),
        "hint": (
            "Diese App läuft nicht in deinem Heimnetz (kein 192.168.x). "
            "RTSP zu Kameras unter 192.168.x funktioniert erst, wenn du die App "
            "lokal auf dem PC startest (./scripts/run-local.sh oder docker compose)."
            if not any(ip.startswith("192.168.") for ip in ips)
            else "App scheint im privaten Netz zu laufen – RTSP sollte erreichbar sein."
        ),
        "private_ips": private,
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
    if not tcp["ok"] and host.startswith("192.168.") and info["looks_like_cloud"]:
        result["recommendation"] = (
            f"Port {port} auf {host} ist von diesem Server aus nicht erreichbar "
            f"(Server-IPs: {', '.join(info['local_ips']) or 'unbekannt'}). "
            "Das Dashboard kommt per Cursor-Port-Forward als localhost an, "
            "aber die Kamera-Verbindung startet vom Cloud-Server – der hat kein "
            "Zugang zu deinem LAN. Bitte lokal starten:\n"
            "  git pull && ./scripts/run-local.sh\n"
            "oder: docker compose up --build -d\n"
            "Dann http://localhost:8088 auf dem PC öffnen."
        )
    elif not tcp["ok"]:
        result["recommendation"] = (
            f"TCP zu {host}:{port} fehlgeschlagen: {tcp['detail']}. "
            "Prüfe IP, RTSP-Port (meist 554), Kamera-Online-Status und Firewall. "
            "Reolink-URL oft: rtsp://USER:PASS@IP:554/h264Preview_01_main "
            f"(aktuell: Pfad '{parsed.path or '/'}')."
        )
    else:
        result["recommendation"] = (
            f"TCP zu {host}:{port} OK. Wenn Preview trotzdem scheitert: "
            "User/Passwort und RTSP-Pfad prüfen "
            "(Reolink z.B. /h264Preview_01_main oder /Preview_01_main)."
        )
    return result
