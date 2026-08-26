"""Live preview endpoints (JPEG frame + MJPEG)."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app.services.rtsp_preview import previews
from app.services.rtsp_recorder import recorder

router = APIRouter(prefix="/api/preview", tags=["preview"])


class PreviewStartIn(BaseModel):
    url: str = Field(..., min_length=8)
    label: str = "Vorschau"


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}


@router.get("")
async def list_previews():
    return {"sessions": previews.list_sessions()}


@router.post("/start")
async def start_preview(body: PreviewStartIn):
    try:
        sess = previews.start_rtsp(body.url.strip(), label=body.label.strip() or "Vorschau")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    # wait briefly for first frame
    for _ in range(25):
        fresh = previews.get(sess.id)
        if fresh and fresh.status == "live":
            break
        if fresh and fresh.status == "error":
            raise HTTPException(400, fresh.error or "Vorschau fehlgeschlagen")
        await asyncio.sleep(0.2)
    fresh = previews.get(sess.id)
    return (fresh or sess).to_dict()


@router.post("/{session_id}/stop")
async def stop_preview(session_id: str):
    previews.stop(session_id)
    return {"stopped": session_id}


@router.get("/{session_id}/frame.jpg")
async def preview_frame(session_id: str):
    sess = previews.get(session_id)
    if not sess:
        raise HTTPException(404, "Vorschau nicht gefunden")
    if not sess.frame_path.exists() or sess.frame_path.stat().st_size < 100:
        if sess.status == "error":
            raise HTTPException(400, sess.error or "Vorschau-Fehler")
        raise HTTPException(404, "Noch kein Frame")
    return FileResponse(
        sess.frame_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@router.get("/{session_id}/status")
async def preview_status(session_id: str):
    sess = previews.get(session_id)
    if not sess:
        raise HTTPException(404, "Vorschau nicht gefunden")
    return sess.to_dict()


@router.get("/{session_id}/mjpeg")
async def preview_mjpeg(session_id: str):
    """Multipart MJPEG stream for smoother live view (from same source)."""
    sess = previews.get(session_id)
    if not sess:
        raise HTTPException(404, "Vorschau nicht gefunden")

    source = sess.source
    kind = sess.kind

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
            "fps=5,scale=960:-2",
            "-q:v",
            "6",
            "-f",
            "mjpeg",
            "-",
        ]
    else:
        if not Path(source).exists():
            raise HTTPException(404, "Aufnahme-Datei fehlt")
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-stream_loop",
            "-1",
            "-i",
            source,
            "-an",
            "-vf",
            "fps=5,scale=960:-2",
            "-q:v",
            "6",
            "-f",
            "mjpeg",
            "-",
        ]

    boundary = b"frame"

    async def generate():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_env(),
        )
        assert proc.stdout is not None
        buf = b""
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                while True:
                    start = buf.find(b"\xff\xd8")
                    end = buf.find(b"\xff\xd9")
                    if start == -1 or end == -1 or end < start:
                        # keep tail
                        if start > 0:
                            buf = buf[start:]
                        break
                    jpg = buf[start : end + 2]
                    buf = buf[end + 2 :]
                    yield (
                        b"--" + boundary + b"\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
                        + jpg + b"\r\n"
                    )
        finally:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    return StreamingResponse(
        generate(),
        media_type=f"multipart/x-mixed-replace; boundary={boundary.decode()}",
        headers={"Cache-Control": "no-cache", "Connection": "close"},
    )


@router.post("/recording/{job_id}/start")
async def start_recording_preview(job_id: str):
    job = recorder.get(job_id)
    if not job:
        raise HTTPException(404, "Aufnahme nicht gefunden")
    if not job.raw_path:
        raise HTTPException(400, "Keine Rohdatei")
    try:
        sess = previews.ensure_recording_preview(job_id, job.raw_path, label=job.name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    if not sess:
        raise HTTPException(400, "Noch zu wenig Daten für Vorschau – kurz warten und erneut versuchen")
    return sess.to_dict()
