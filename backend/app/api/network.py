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


def _ipv4_tuple(ip: str) -> tuple[int, int, int, int] | None:
    try:
        parts = [int(p) for p in ip.split(".")]
        if len(parts) == 4 and all(0 <= p <= 255 for p in parts):
            return parts[0], parts[1], parts[2], parts[3]
    except ValueError:
        return None
    return None


def _same_slash24(a: str, b: str) -> bool:
    ta, tb = _ipv4_tuple(a), _ipv4_tuple(b)
    if not ta or not tb:
        return False
    return ta[:3] == tb[:3]


def _in_docker() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8") as fh:
            text = fh.read()
        return "docker" in text or "containerd" in text
    except OSError:
        return False


def _env_label(ips: list[str]) -> tuple[str, str]:
    """Return (kind, human label). kind: host_lan | docker | cloud | unknown"""
    if os.getenv("FORCE_LAN_OK", "").lower() in ("1", "true", "yes"):
        return "host_lan", "lokal (FORCE_LAN_OK)"
    in_docker = _in_docker()
    if in_docker:
        return "docker", "Docker-Container (nicht Host-LAN)"
    if any(ip.startswith("192.168.") for ip in ips):
        return "host_lan", "lokales Netz (192.168.x)"
    if any(ip.startswith("10.") for ip in ips):
        return "host_lan", "privates Netz (10.x)"
    return "cloud", "kein Heimnetz-IP (Cloud / fremdes Netz)"


@router.get("/info")
async def network_info():
    ips = _local_ips()
    kind, label = _env_label(ips)
    return {
        "hostname": socket.gethostname(),
        "local_ips": ips,
        "env_kind": kind,
        "looks_like_cloud": kind == "cloud",
        "in_docker": kind == "docker" or _in_docker(),
        "env_label": label,
        "dashboard_url": LOCAL_DASHBOARD,
        "hint": {
            "cloud": (
                "Die Kamera unter 192.168.x ist nur erreichbar, wenn die App auf deinem "
                f"PC im gleichen WLAN/LAN läuft → {LOCAL_DASHBOARD} nach lokalem Start."
            ),
            "docker": (
                "App läuft in Docker. Wenn die Kamera Timeout meldet: oft blockiert "
                "Docker Desktop den Zugriff aufs Heimnetz. Dann scripts\\run-local.bat "
                f"nutzen (Windows-Netz) → {LOCAL_DASHBOARD}."
            ),
            "host_lan": "App scheint auf dem Host im privaten Netz zu laufen.",
            "unknown": "Netzwerkumgebung unklar.",
        }.get(kind, "Netzwerkumgebung unklar."),
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
        "needs_host_network": False,
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
    lan_cam = bool(
        host.startswith("192.168.") or host.startswith("10.") or host.startswith("172.")
    )
    if tcp["ok"]:
        result["looks_like_cloud"] = False
        result["env_label"] = "Kamera erreichbar"
        result["recommendation"] = (
            f"TCP zu {host}:{port} OK. Wenn Preview trotzdem scheitert: "
            "User/Passwort und RTSP-Pfad prüfen "
            "(Reolink z.B. /h264Preview_01_main, /Preview_01_main oder /11)."
        )
        return result

    server_ips = info["local_ips"] or []
    different_subnet = bool(server_ips) and not any(
        _same_slash24(ip, host) for ip in server_ips
    )
    in_docker = bool(info.get("in_docker")) or info.get("env_kind") == "docker"

    if lan_cam and (in_docker or different_subnet):
        result["needs_host_network"] = True
        result["needs_local_restart"] = False
        result["env_label"] = (
            "Docker-Netz, Kamera in anderem Subnetz"
            if in_docker
            else "anderes Subnetz als Kamera"
        )
        result["recommendation"] = (
            f"Port {port} auf {host} Timeout von Server-IP "
            f"{', '.join(server_ips) or 'unbekannt'}.\n"
            f"Container/App und Kamera sind in unterschiedlichen Netzen "
            f"(z.B. Docker 192.168.96.x vs Kamera 192.168.1.x).\n\n"
            "Das ist KEIN Cursor-Cloud-Problem mehr – Docker Desktop "
            "reicht die LAN-Kamera oft nicht durch.\n\n"
            "Sofort-Lösung (nutzt Windows-Netz direkt):\n"
            "  1. docker compose down\n"
            "  2. scripts\\run-local.bat\n"
            f"  3. Browser: {LOCAL_DASHBOARD}\n\n"
            "Prüfen von Windows (PowerShell):\n"
            f"  Test-NetConnection {host} -Port {port}\n"
            "  → TcpTestSucceeded=True? Dann Kamera OK, nur Docker blockiert.\n"
            "  → False? PC/Kamera gleiches WLAN, VPN aus, Firewall prüfen.\n\n"
            "Optional Docker: Settings → Enable host networking, dann\n"
            "  set DOCKER_HOST_NETWORK=1 && scripts\\start-docker.bat"
        )
        return result

    if lan_cam and info.get("env_kind") == "cloud":
        result["needs_local_restart"] = True
        result["recommendation"] = (
            f"Port {port} auf {host} nicht erreichbar "
            f"(Server-IPs: {', '.join(server_ips) or 'unbekannt'}).\n"
            "Das wirkt wie Cursor-Cloud / fremdes Netz.\n"
            "Lokal: scripts\\start-docker.bat oder scripts\\run-local.bat → "
            f"{LOCAL_DASHBOARD}"
        )
        return result

    result["recommendation"] = (
        f"TCP zu {host}:{port} fehlgeschlagen: {tcp['detail']}. "
        "Prüfe IP, RTSP-Port (meist 554), Kamera-Online-Status und Firewall. "
        "Reolink-URL oft: rtsp://USER:PASS@IP:554/h264Preview_01_main "
        f"(aktuell: Pfad '{parsed.path or '/'}')."
    )
    return result
