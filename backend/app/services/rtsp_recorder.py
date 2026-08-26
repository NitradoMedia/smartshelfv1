"""RTSP stream recorder via ffmpeg — start, auto-stop by duration, manual stop."""

from __future__ import annotations

import logging
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
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    _timer: Optional[threading.Timer] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        elapsed = None
        if self.started_at:
            end = self.ended_at or datetime.now(timezone.utc)
            elapsed = max(0, int((end - self.started_at).total_seconds()))
        return {
            "id": self.id,
            "name": self.name,
            "rtsp_url": _redact_url(self.rtsp_url),
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "max_seconds": self.max_seconds,
            "elapsed_seconds": elapsed,
            "status": self.status,
            "output_path": self.output_path,
            "output_url": f"/api/recordings/file/{Path(self.output_path).name}"
            if self.output_path and self.status == "finished"
            else None,
            "error": self.error,
        }


def _redact_url(url: str) -> str:
    # rtsp://user:pass@host → rtsp://user:***@host
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
    ) -> RecordingJob:
        url = (rtsp_url or "").strip()
        if not url.lower().startswith("rtsp://"):
            raise ValueError("RTSP-URL muss mit rtsp:// beginnen")

        settings = get_settings()
        settings.recordings_dir.mkdir(parents=True, exist_ok=True)
        settings.videos_dir.mkdir(parents=True, exist_ok=True)

        job_id = uuid.uuid4().hex[:12]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in (name or "kamera"))[:40]
        raw_path = settings.recordings_dir / f"{safe_name}_{stamp}_{job_id}.raw.mp4"
        final_path = settings.recordings_dir / f"{safe_name}_{stamp}_{job_id}.mp4"

        # Fragmented MP4 so the file stays usable if ffmpeg is killed
        # Prefer stream-copy (cameras usually already H.264); remux to browser MP4 on stop.
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            url,
            "-c",
            "copy",
            "-an",
            "-f",
            "mp4",
            "-movflags",
            "+frag_keyframe+empty_moov+default_base_moof",
            str(raw_path),
        ]
        try:
            env = {k: v for k, v in __import__("os").environ.items() if k != "LD_LIBRARY_PATH"}
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=env,
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

        # Watchdog thread: detect crash + optional duration stop
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
                # unexpected exit
                err = ""
                try:
                    err = (proc.stderr.read() or b"").decode("utf-8", errors="ignore")[-400:]
                except Exception:  # noqa: BLE001
                    pass
                with self._lock:
                    if job.status == "recording":
                        job.status = "failed"
                        job.error = err or f"ffmpeg exit {proc.returncode}"
                        job.ended_at = datetime.now(timezone.utc)
                logger.error("RTSP recording failed id=%s: %s", job_id, job.error)
                return
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
                # Graceful quit for ffmpeg
                if proc.stdin:
                    try:
                        proc.stdin.write(b"q")
                        proc.stdin.flush()
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.send_signal(signal.SIGINT)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=3)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error stopping ffmpeg for %s: %s", job_id, exc)

        raw: Path = job.raw_path  # type: ignore[assignment]
        final: Path = job.final_path  # type: ignore[assignment]
        try:
            if raw and raw.exists() and raw.stat().st_size > 0:
                to_browser_mp4(raw, final)
                raw.unlink(missing_ok=True)
                if job.copy_to_videos and final:
                    settings = get_settings()
                    dest = settings.videos_dir / final.name
                    dest.write_bytes(final.read_bytes())
                job.output_path = str(final)
                job.status = "finished"
            else:
                job.status = "failed"
                job.error = job.error or "Keine Videodaten empfangen (Stream leer/unerreichbar?)"
                if raw:
                    raw.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = str(exc)
            logger.exception("Finalize recording failed id=%s", job_id)

        job.ended_at = datetime.now(timezone.utc)
        logger.info("RTSP recording stopped id=%s reason=%s status=%s", job_id, reason, job.status)
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
