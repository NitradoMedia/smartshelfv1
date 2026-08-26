from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Transaction
from app.services.matcher import MatcherService
from app.services.pos_parser import PosTransaction

router = APIRouter(prefix="/api", tags=["ops"])


class TransactionOut(BaseModel):
    id: int
    external_id: str
    timestamp: datetime
    article_count: int
    total_amount: Optional[float]
    cashier: Optional[str]
    register_id: Optional[str]
    processed: bool

    class Config:
        from_attributes = True


class ScanResult(BaseModel):
    ingested: int
    incidents_created: int


@router.get("/transactions", response_model=list[TransactionOut])
async def list_transactions(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(Transaction).order_by(Transaction.timestamp.desc()).limit(200))
    ).scalars()
    return list(rows)


@router.post("/scan", response_model=ScanResult)
async def scan_now(db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    matcher = MatcherService(settings)
    ingested = await matcher.ingest_pos_directory(db)
    incidents = await matcher.process_pending(db)
    return ScanResult(ingested=ingested, incidents_created=len(incidents))


@router.post("/upload-pos")
async def upload_pos(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    dest = settings.pos_watch_dir / file.filename
    content = await file.read()
    dest.write_bytes(content)
    matcher = MatcherService(settings)
    ingested = await matcher.ingest_file(db, dest)
    incidents = await matcher.process_pending(db)
    return {"ingested": ingested, "incidents_created": len(incidents), "file": file.filename}


@router.post("/manual-check")
async def manual_check(
    external_id: str = Form(...),
    timestamp: str = Form(...),
    article_count: int = Form(...),
    video: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    matcher = MatcherService(settings)
    from dateutil import parser as date_parser

    tmp = settings.uploads_dir / video.filename
    tmp.write_bytes(await video.read())
    tx = PosTransaction(
        external_id=external_id,
        timestamp=date_parser.parse(timestamp, dayfirst=True),
        article_count=article_count,
    )
    incident = await matcher.reprocess_with_upload(db, tx, tmp)
    return {
        "mismatch": incident is not None,
        "incident_id": incident.id if incident else None,
        "ai_articles": incident.ai_articles if incident else article_count,
        "receipt_articles": article_count,
    }


@router.get("/health")
async def health():
    settings = get_settings()
    return {
        "status": "ok",
        "demo_mode": settings.demo_mode,
        "ai_backend": settings.ai_backend,
        "reolink_configured": bool(settings.reolink_host or settings.reolink_rtsp_url),
    }
