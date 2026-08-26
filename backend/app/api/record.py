"""RTSP recording API: sources, start/stop, list files."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.rtsp_recorder import recorder
from app.services.runtime_settings import load_runtime, save_runtime

router = APIRouter(prefix="/api", tags=["record"])


class RtspSourceIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    url: str = Field(..., min_length=8)


class StartRecordIn(BaseModel):
    url: Optional[str] = None
    source_name: Optional[str] = None
    name: str = "kamera"
    max_seconds: Optional[int] = Field(default=None, ge=5, le=86400)
    copy_to_videos: bool = True
    save_source: bool = True


@router.get("/rtsp/sources")
async def list_sources():
    data = load_runtime()
    return {"sources": data.get("rtsp_sources") or []}


@router.put("/rtsp/sources")
async def put_sources(sources: list[RtspSourceIn]):
    cleaned = []
    for s in sources:
        url = s.url.strip()
        if not url.lower().startswith("rtsp://"):
            raise HTTPException(400, f"Ungültige RTSP-URL: {s.name}")
        cleaned.append({"name": s.name.strip(), "url": url})
    save_runtime({"rtsp_sources": cleaned})
    return {"sources": cleaned}


@router.post("/rtsp/sources")
async def add_source(body: RtspSourceIn):
    data = load_runtime()
    sources = list(data.get("rtsp_sources") or [])
    url = body.url.strip()
    if not url.lower().startswith("rtsp://"):
        raise HTTPException(400, "RTSP-URL muss mit rtsp:// beginnen")
    # replace same name
    sources = [s for s in sources if s.get("name") != body.name.strip()]
    sources.append({"name": body.name.strip(), "url": url})
    save_runtime({"rtsp_sources": sources})
    return {"sources": sources}


@router.delete("/rtsp/sources/{name}")
async def delete_source(name: str):
    data = load_runtime()
    sources = [s for s in (data.get("rtsp_sources") or []) if s.get("name") != name]
    save_runtime({"rtsp_sources": sources})
    return {"sources": sources}


@router.get("/recordings")
async def list_recordings():
    settings = get_settings()
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for p in sorted(settings.recordings_dir.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.name.endswith(".raw.mp4"):
            continue
        files.append(
            {
                "name": p.name,
                "size": p.stat().st_size,
                "mtime": p.stat().st_mtime,
                "url": f"/api/recordings/file/{p.name}",
            }
        )
    return {"active": recorder.list_jobs(), "files": files}


@router.post("/recordings/start")
async def start_recording(body: StartRecordIn):
    url = (body.url or "").strip()
    if not url and body.source_name:
        sources = load_runtime().get("rtsp_sources") or []
        match = next((s for s in sources if s.get("name") == body.source_name), None)
        if not match:
            raise HTTPException(404, f"Quelle nicht gefunden: {body.source_name}")
        url = match["url"]
        name = body.name or body.source_name
    else:
        name = body.name

    if not url:
        raise HTTPException(400, "RTSP-URL oder gespeicherte Quelle angeben")

    if body.save_source and url:
        sources = list(load_runtime().get("rtsp_sources") or [])
        sources = [s for s in sources if s.get("name") != name]
        sources.append({"name": name, "url": url})
        save_runtime({"rtsp_sources": sources})

    try:
        job = recorder.start(
            rtsp_url=url,
            name=name,
            max_seconds=body.max_seconds,
            copy_to_videos=body.copy_to_videos,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
    return job.to_dict()


@router.post("/recordings/{job_id}/stop")
async def stop_recording(job_id: str):
    try:
        job = recorder.stop(job_id, reason="manual")
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return job.to_dict()


@router.get("/recordings/active")
async def active_recordings():
    return recorder.list_jobs()


@router.get("/recordings/file/{filename}")
async def get_recording_file(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Ungültiger Dateiname")
    path = get_settings().recordings_dir / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=path.name,
        content_disposition_type="inline",
        headers={"Accept-Ranges": "bytes"},
    )


@router.delete("/recordings/file/{filename}")
async def delete_recording_file(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Ungültiger Dateiname")
    path = get_settings().recordings_dir / filename
    if not path.exists():
        raise HTTPException(404, "Datei nicht gefunden")
    path.unlink()
    return {"deleted": filename}
