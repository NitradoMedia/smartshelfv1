"""Persist runtime settings (FTP, RTSP sources, etc.) to JSON under DATA_DIR."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.ftp_client import FtpConfig

_lock = threading.Lock()
_DEFAULTS: dict[str, Any] = {
    "ftp": {
        "enabled": False,
        "host": "",
        "port": 21,
        "user": "",
        "password": "",
        "remote_dir": "/",
        "passive": True,
        "timeout": 30,
        "match_window_seconds": 180,
    },
    "video_source": "auto",  # auto | upload | ftp | reolink | demo
    "rtsp_sources": [],  # [{name, url}, ...]
}


def _path() -> Path:
    return get_settings().data_dir / "runtime_settings.json"


def load_runtime() -> dict[str, Any]:
    path = _path()
    data = json.loads(json.dumps(_DEFAULTS))
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                if "ftp" in stored and isinstance(stored["ftp"], dict):
                    data["ftp"].update(stored["ftp"])
                if "video_source" in stored:
                    data["video_source"] = stored["video_source"]
                if "rtsp_sources" in stored and isinstance(stored["rtsp_sources"], list):
                    data["rtsp_sources"] = stored["rtsp_sources"]
        except Exception:  # noqa: BLE001
            pass
    settings = get_settings()
    ftp = data["ftp"]
    if not ftp.get("host") and settings.ftp_host:
        ftp["host"] = settings.ftp_host
        ftp["port"] = settings.ftp_port
        ftp["user"] = settings.ftp_user
        ftp["password"] = settings.ftp_password
        ftp["remote_dir"] = settings.ftp_remote_dir
        ftp["passive"] = settings.ftp_passive
        ftp["enabled"] = settings.ftp_enabled
    return data


def save_runtime(patch: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        current = load_runtime()
        if "ftp" in patch and isinstance(patch["ftp"], dict):
            incoming = dict(patch["ftp"])
            if incoming.get("password") in (None, ""):
                incoming.pop("password", None)
            current["ftp"].update(incoming)
        if "video_source" in patch:
            current["video_source"] = patch["video_source"]
        if "rtsp_sources" in patch and isinstance(patch["rtsp_sources"], list):
            current["rtsp_sources"] = patch["rtsp_sources"]
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        return current


def get_ftp_config() -> FtpConfig:
    ftp = load_runtime()["ftp"]
    return FtpConfig(
        enabled=bool(ftp.get("enabled")),
        host=str(ftp.get("host") or ""),
        port=int(ftp.get("port") or 21),
        user=str(ftp.get("user") or "anonymous"),
        password=str(ftp.get("password") or ""),
        remote_dir=str(ftp.get("remote_dir") or "/"),
        passive=bool(ftp.get("passive", True)),
        timeout=int(ftp.get("timeout") or 30),
    )


def public_settings() -> dict[str, Any]:
    data = load_runtime()
    ftp = dict(data["ftp"])
    if ftp.get("password"):
        ftp["password_set"] = True
        ftp["password"] = ""
    else:
        ftp["password_set"] = False
    # redact passwords in RTSP URLs for UI display of stored list — keep full URL
    # in API for editing; UI uses separate sources endpoint
    return {
        "ftp": ftp,
        "video_source": data.get("video_source", "auto"),
        "rtsp_sources": data.get("rtsp_sources") or [],
    }
