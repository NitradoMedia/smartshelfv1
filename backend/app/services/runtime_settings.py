"""Persist runtime settings (FTP etc.) to JSON under DATA_DIR."""

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
        except Exception:  # noqa: BLE001
            pass
    # Env overrides as bootstrap defaults when file empty
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
            # keep password if blank submitted
            incoming = dict(patch["ftp"])
            if incoming.get("password") in (None, ""):
                incoming.pop("password", None)
            current["ftp"].update(incoming)
        if "video_source" in patch:
            current["video_source"] = patch["video_source"]
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
    return {"ftp": ftp, "video_source": data.get("video_source", "auto")}
