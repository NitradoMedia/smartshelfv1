from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Incident
from app.services.video_encode import to_browser_mp4

router = APIRouter(prefix="/api/media", tags=["media"])


def _clip_path(inc: Incident) -> Path:
    if not inc or not inc.clip_path:
        raise HTTPException(404, "Clip not found")
    path = Path(inc.clip_path)
    if not path.exists():
        raise HTTPException(404, "Clip file missing")
    try:
        to_browser_mp4(path)
    except Exception:  # noqa: BLE001
        pass
    return path


@router.api_route("/clip/{incident_id}", methods=["GET", "HEAD"])
async def get_clip(incident_id: int, db: AsyncSession = Depends(get_db)):
    inc = await db.get(Incident, incident_id)
    path = _clip_path(inc)
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=path.name,
        content_disposition_type="inline",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )


@router.get("/thumb/{incident_id}")
async def get_thumb(incident_id: int, db: AsyncSession = Depends(get_db)):
    inc = await db.get(Incident, incident_id)
    if not inc or not inc.thumbnail_path:
        raise HTTPException(404, "Thumbnail not found")
    path = Path(inc.thumbnail_path)
    if not path.exists():
        raise HTTPException(404, "Thumbnail file missing")
    return FileResponse(path, media_type="image/jpeg", content_disposition_type="inline")
