from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Incident, IncidentStatus

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


class IncidentOut(BaseModel):
    id: int
    external_id: str
    receipt_time: datetime
    receipt_articles: int
    ai_articles: int
    difference: int
    status: str
    clip_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    ai_backend: Optional[str] = None
    ai_details: Optional[str] = None
    notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ReviewIn(BaseModel):
    status: str = Field(..., pattern="^(false_alarm|theft|open)$")
    notes: Optional[str] = None


class StatsOut(BaseModel):
    open: int
    false_alarm: int
    theft: int
    total: int


def _to_out(inc: Incident) -> IncidentOut:
    return IncidentOut(
        id=inc.id,
        external_id=inc.external_id,
        receipt_time=inc.receipt_time,
        receipt_articles=inc.receipt_articles,
        ai_articles=inc.ai_articles,
        difference=inc.difference,
        status=inc.status,
        clip_url=f"/api/media/clip/{inc.id}" if inc.clip_path else None,
        thumbnail_url=f"/api/media/thumb/{inc.id}" if inc.thumbnail_path else None,
        ai_backend=inc.ai_backend,
        ai_details=inc.ai_details,
        notes=inc.notes,
        reviewed_at=inc.reviewed_at,
        created_at=inc.created_at,
    )


@router.get("", response_model=list[IncidentOut])
async def list_incidents(
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Incident).order_by(Incident.created_at.desc())
    if status:
        q = q.where(Incident.status == status)
    rows = (await db.execute(q)).scalars().all()
    return [_to_out(r) for r in rows]


@router.get("/stats", response_model=StatsOut)
async def stats(db: AsyncSession = Depends(get_db)):
    async def count(status: Optional[str] = None) -> int:
        q = select(func.count()).select_from(Incident)
        if status:
            q = q.where(Incident.status == status)
        return int(await db.scalar(q) or 0)

    return StatsOut(
        open=await count(IncidentStatus.open.value),
        false_alarm=await count(IncidentStatus.false_alarm.value),
        theft=await count(IncidentStatus.theft.value),
        total=await count(),
    )


@router.get("/{incident_id}", response_model=IncidentOut)
async def get_incident(incident_id: int, db: AsyncSession = Depends(get_db)):
    inc = await db.get(Incident, incident_id)
    if not inc:
        raise HTTPException(404, "Incident not found")
    return _to_out(inc)


@router.post("/{incident_id}/review", response_model=IncidentOut)
async def review_incident(
    incident_id: int, body: ReviewIn, db: AsyncSession = Depends(get_db)
):
    inc = await db.get(Incident, incident_id)
    if not inc:
        raise HTTPException(404, "Incident not found")
    inc.status = body.status
    inc.notes = body.notes
    inc.reviewed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(inc)
    return _to_out(inc)
