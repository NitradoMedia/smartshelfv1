"""Live RTSP / recording preview via ffmpeg JPEG snapshots (+ optional MJPEG)."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}


@dataclass
class PreviewSession:
    id: str
    source: str  # rtsp url or file path
    kind: str  # rtsp | file
    label: str
    started_at: float
    frame_path: Path
    status: str = "starting"  # starting | live | error | stopped
    error: Optional[str] = None
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    frames: int = 0

    def to_dict(self) -> dict:
        age = None
        if self.frame_path.exists():
            age = max(0.0, time.time() - self.frame_path.stat().st_mtime)
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "error": self.error,
            "frames": self.frames,
            "frame_age_sec": age,
            "frame_url": f"/api/preview/{self.id}/frame.jpg",
            "mjpeg_url": f"/api/preview/{self.id}/mjpeg",
        }


class PreviewManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, PreviewSession] = {}

    def _preview_dir(self) -> Path:
        d = get_settings().data_dir / "preview"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list_sessions(self) -> list[dict]:
        with self._lock:
            return [s.to_dict() for s in self._sessions.values()]

    def get(self, session_id: str) -> Optional[PreviewSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def start_rtsp(self, url: str, label: str = "Vorschau") -> PreviewSession:
        url = (url or "").strip()
        if not url.lower().startswith("rtsp://"):
            raise ValueError("RTSP-URL muss mit rtsp:// beginnen")
        return self._start(source=url, kind="rtsp", label=label)

    def start_file(self, path: Path, label: str = "Aufnahme") -> PreviewSession:
        if not path.exists():
            raise FileNotFoundError(f"Datei fehlt: {path}")
        return self._start(source=str(path), kind="file", label=label)

    def _start(self, source: str, kind: str, label: str) -> PreviewSession:
        # Replace existing session with same label+kind to avoid leaks
        with self._lock:
            for sid, sess in list(self._sessions.items()):
                if sess.label == label and sess.kind == kind and sess.status in {"starting", "live"}:
                    self._stop_unlocked(sid)

        sid = uuid.uuid4().hex[:10]
        frame_path = self._preview_dir() / f"{sid}.jpg"
        frame_path.unlink(missing_ok=True)

        session = PreviewSession(
            id=sid,
            source=source,
            kind=kind,
            label=label,
            started_at=time.time(),
            frame_path=frame_path,
            status="starting",
        )
        with self._lock:
            self._sessions[sid] = session

        if kind == "rtsp":
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-rtsp_transport",
                "tcp",
                "-i",
                source,
                "-an",
                "-vf",
                "fps=2,scale=960:-2",
                "-q:v",
                "5",
                "-update",
                "1",
                "-y",
                str(frame_path),
            ]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=_env(),
            )
            session.process = proc
            threading.Thread(target=self._watch, args=(sid,), daemon=True).start()
        else:
            threading.Thread(target=self._file_poll_loop, args=(sid,), daemon=True).start()

        logger.info("Preview started id=%s kind=%s label=%s", sid, kind, label)
        return session

    def _file_poll_loop(self, session_id: str) -> None:
        """Pull latest frame from a (possibly still growing) recording file."""
        while True:
            sess = self.get(session_id)
            if not sess or sess.status == "stopped":
                return
            src = Path(sess.source)
            if not src.exists() or src.stat().st_size < 2048:
                time.sleep(0.5)
                continue
            tmp = sess.frame_path.with_suffix(".tmp.jpg")
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-sseof",
                "-1.5",
                "-i",
                str(src),
                "-frames:v",
                "1",
                "-q:v",
                "5",
                "-y",
                str(tmp),
            ]
            try:
                subprocess.run(cmd, check=False, capture_output=True, timeout=8, env=_env())
                if tmp.exists() and tmp.stat().st_size > 200:
                    tmp.replace(sess.frame_path)
                    with self._lock:
                        sess.status = "live"
                        sess.frames += 1
                else:
                    tmp.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                tmp.unlink(missing_ok=True)
            time.sleep(0.6)

    def _watch(self, session_id: str) -> None:
        deadline = time.time() + 12
        while True:
            sess = self.get(session_id)
            if not sess or sess.status == "stopped":
                return
            proc = sess.process
            if proc and proc.poll() is not None:
                err = ""
                try:
                    err = (proc.stderr.read() or b"").decode("utf-8", errors="ignore")[-400:]
                except Exception:  # noqa: BLE001
                    pass
                with self._lock:
                    if sess.status != "stopped":
                        sess.status = "error"
                        low = err.lower()
                        if any(x in low for x in ("timed out", "connection refused", "no route")):
                            sess.error = (
                                "Stream nicht erreichbar – App muss im gleichen LAN "
                                "wie die Kamera laufen."
                            )
                        else:
                            sess.error = err.strip() or "Vorschau beendet"
                return
            if sess.frame_path.exists() and sess.frame_path.stat().st_size > 500:
                with self._lock:
                    if sess.status == "starting":
                        sess.status = "live"
                    sess.frames += 1
            elif time.time() > deadline and sess.status == "starting":
                with self._lock:
                    sess.status = "error"
                    sess.error = "Kein Frame empfangen (Stream leer / unerreichbar?)"
                self.stop(session_id)
                return
            time.sleep(0.4)

    def stop(self, session_id: str) -> None:
        with self._lock:
            self._stop_unlocked(session_id)

    def _stop_unlocked(self, session_id: str) -> None:
        sess = self._sessions.get(session_id)
        if not sess:
            return
        sess.status = "stopped"
        proc = sess.process
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:  # noqa: BLE001
                pass
        # keep last frame for a bit; delete later
        logger.info("Preview stopped id=%s", session_id)

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self._sessions.keys())
        for sid in ids:
            self.stop(sid)

    def ensure_recording_preview(self, job_id: str, raw_path: Path, label: str) -> Optional[PreviewSession]:
        """Attach/replace a file-preview for an active recording."""
        # Wait briefly for first bytes
        for _ in range(20):
            if raw_path.exists() and raw_path.stat().st_size > 4096:
                break
            time.sleep(0.25)
        if not raw_path.exists() or raw_path.stat().st_size < 1024:
            return None
        label = f"rec:{job_id}"
        return self.start_file(raw_path, label=label)


previews = PreviewManager()
