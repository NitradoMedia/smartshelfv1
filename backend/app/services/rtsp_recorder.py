"""RTSP stream recorder via ffmpeg — start, auto-stop by duration, manual stop."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.services.video_encode import to_browser_mp4

logger = logging.getLogger(__name__)


def _ffmpeg_env() -> dict:
    return {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}


@dataclass
class RecordingJob:
    id: str
    name: str
    rtsp_url: str
    started_at: datetime
    max_seconds: Optional[int]
    status: str = "recording"  # recording | stopping | finished | failed
    output_path: Optional[str] = None
    error: Optional[str] = None
    ended_at: Optional[datetime] = None
    copy_to_videos: bool = True
    raw_path: Optional[Path] = None
    final_path: Optional[Path] = None
    bytes_written: int = 0
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    _timer: Optional[threading.Timer] = field(default=None, repr=False)

    def refresh_size(self) -> int:
        path = self.raw_path
        if path and path.exists():
            self.bytes_written = path.stat().st_size
        elif self.final_path and self.final_path.exists():
            self.bytes_written = self.final_path.stat().st_size
        return self.bytes_written

    def to_dict(self) -> dict:
        elapsed = None
        if self.started_at:
            end = self.ended_at or datetime.now(timezone.utc)
            elapsed = max(0, int((end - self.started_at).total_seconds()))
        self.refresh_size()
        out_name = Path(self.output_path).name if self.output_path else None
        return {
            "id": self.id,
            "name": self.name,
            "rtsp_url": _redact_url(self.rtsp_url),
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "max_seconds": self.max_seconds,
            "elapsed_seconds": elapsed,
            "status": self.status,
            "bytes_written": self.bytes_written,
            "output_path": self.output_path,
            "output_url": (
                f"/api/recordings/file/{out_name}"
                if out_name and self.status == "finished"
                else None
            ),
            "error": self.error,
        }


def _redact_url(url: str) -> str:
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    creds, host = rest.rsplit("@", 1)
    if ":" in creds:
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return f"{scheme}://***@{host}"


def probe_rtsp(url: str, timeout_sec: float = 8.0) -> tuple[bool, str]:
    """Quick check whether ffmpeg can open the RTSP URL."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "5000000",
        "-show_entries",
        "stream=codec_type,codec_name",
        "-of",
        "csv=p=0",
        url,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=_ffmpeg_env(),
        )
    except subprocess.TimeoutExpired:
        return False, "RTSP-Timeout – Kamera nicht erreichbar (gleiches LAN / Firewall?)"
    except FileNotFoundError:
        return False, "ffprobe/ffmpeg fehlt"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "unbekannt").strip()[-300:]
        low = err.lower()
        if any(x in low for x in ("connection refused", "no route", "network is unreachable", "connection timed out", "timed out")):
            return False, (
                "Kamera nicht erreichbar von diesem Rechner. "
                "Die App muss im gleichen LAN wie die Kamera laufen "
                "(lokal starten – Cloud-Port-Forward reicht für RTSP nicht)."
            )
        return False, err or f"ffprobe exit {proc.returncode}"
    if "video" not in (proc.stdout or "").lower() and "h264" not in (proc.stdout or "").lower():
        # some probes only print codec names
        if not (proc.stdout or "").strip():
            return False, "Kein Videostream in der RTSP-URL gefunden"
    return True, (proc.stdout or "").strip()


class RtspRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, RecordingJob] = {}

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda x: x.started_at, reverse=True)]

    def get(self, job_id: str) -> Optional[RecordingJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def start(
        self,
        rtsp_url: str,
        name: str = "kamera",
        max_seconds: Optional[int] = None,
        copy_to_videos: bool = True,
        probe_first: bool = True,
    ) -> RecordingJob:
        url = (rtsp_url or "").strip()
        if not url.lower().startswith("rtsp://"):
            raise ValueError("RTSP-URL muss mit rtsp:// beginnen")

        if probe_first:
            ok, msg = probe_rtsp(url)
            if not ok:
                raise RuntimeError(msg)

        settings = get_settings()
        settings.recordings_dir.mkdir(parents=True, exist_ok=True)
        settings.videos_dir.mkdir(parents=True, exist_ok=True)

        job_id = uuid.uuid4().hex[:12]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in (name or "kamera"))[:40]
        # MPEG-TS is much more reliable for live RTSP than fragmented MP4 + copy
        raw_path = settings.recordings_dir / f"{safe_name}_{stamp}_{job_id}.ts"
        final_path = settings.recordings_dir / f"{safe_name}_{stamp}_{job_id}.mp4"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-i",
            url,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "baseline",
            "-g",
            "30",
            "-an",
            "-f",
            "mpegts",
            str(raw_path),
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=_ffmpeg_env(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg nicht gefunden") from exc

        job = RecordingJob(
            id=job_id,
            name=safe_name,
            rtsp_url=url,
            started_at=datetime.now(timezone.utc),
            max_seconds=max_seconds if max_seconds and max_seconds > 0 else None,
            status="recording",
            output_path=str(final_path),
            process=proc,
            copy_to_videos=copy_to_videos,
            raw_path=raw_path,
            final_path=final_path,
        )

        with self._lock:
            self._jobs[job_id] = job

        threading.Thread(target=self._watch, args=(job_id,), daemon=True).start()

        if job.max_seconds:
            timer = threading.Timer(job.max_seconds, lambda: self.stop(job_id, reason="max_duration"))
            timer.daemon = True
            job._timer = timer
            timer.start()

        logger.info("RTSP recording started id=%s name=%s max=%s", job_id, safe_name, job.max_seconds)
        return job

    def _watch(self, job_id: str) -> None:
        while True:
            job = self.get(job_id)
            if not job or job.status != "recording":
                return
            proc = job.process
            if proc and proc.poll() is not None:
                err = ""
                try:
                    err = (proc.stderr.read() or b"").decode("utf-8", errors="ignore")[-500:]
                except Exception:  # noqa: BLE001
                    pass
                # If we already have data, finalize as success
                size = job.refresh_size()
                if size > 1024:
                    try:
                        self.stop(job_id, reason="ffmpeg_exit")
                    except Exception:  # noqa: BLE001
                        logger.exception("finalize after ffmpeg exit failed")
                    return
                with self._lock:
                    if job.status == "recording":
                        job.status = "failed"
                        job.error = err.strip() or f"ffmpeg exit {proc.returncode}"
                        job.ended_at = datetime.now(timezone.utc)
                logger.error("RTSP recording failed id=%s: %s", job_id, job.error)
                return
            job.refresh_size()
            time.sleep(1)

    def stop(self, job_id: str, reason: str = "manual") -> RecordingJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(f"Aufnahme nicht gefunden: {job_id}")
            if job.status not in {"recording", "stopping"}:
                return job
            job.status = "stopping"
            if job._timer:
                job._timer.cancel()

        proc = job.process
        if proc and proc.poll() is None:
            try:
                if proc.stdin:
                    try:
                        proc.stdin.write(b"q")
                        proc.stdin.flush()
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.send_signal(signal.SIGINT)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=3)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error stopping ffmpeg for %s: %s", job_id, exc)

        raw = job.raw_path
        final = job.final_path
        try:
            size = raw.stat().st_size if raw and raw.exists() else 0
            job.bytes_written = size
            if raw and size > 1024 and final:
                to_browser_mp4(raw, final)
                raw.unlink(missing_ok=True)
                if job.copy_to_videos:
                    settings = get_settings()
                    dest = settings.videos_dir / final.name
                    dest.write_bytes(final.read_bytes())
                job.output_path = str(final)
                job.status = "finished"
            else:
                job.status = "failed"
                if not job.error:
                    job.error = (
                        "Keine Videodaten empfangen. Prüfe RTSP-URL und ob die App "
                        "im gleichen Netzwerk wie die Kamera läuft."
                    )
                if raw:
                    raw.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = str(exc)
            logger.exception("Finalize recording failed id=%s", job_id)

        job.ended_at = datetime.now(timezone.utc)
        logger.info(
            "RTSP recording stopped id=%s reason=%s status=%s bytes=%s",
            job_id,
            reason,
            job.status,
            job.bytes_written,
        )
        return job

    def stop_all(self) -> None:
        with self._lock:
            ids = [j.id for j in self._jobs.values() if j.status == "recording"]
        for job_id in ids:
            try:
                self.stop(job_id, reason="shutdown")
            except Exception:  # noqa: BLE001
                logger.exception("Failed stopping job %s", job_id)


recorder = RtspRecorder()
