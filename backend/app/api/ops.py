from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Transaction
from app.services.ftp_client import FtpVideoClient
from app.services.matcher import MatcherService
from app.services.pos_parser import PosTransaction
from app.services.runtime_settings import get_ftp_config, public_settings, save_runtime

router = APIRouter(prefix="/api", tags=["ops"])


class TransactionOut(BaseModel):
    id: int
    external_id: str
    timestamp: object
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


class FtpIn(BaseModel):
    enabled: Optional[bool] = None
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    password: Optional[str] = None
    remote_dir: Optional[str] = None
    passive: Optional[bool] = None
    timeout: Optional[int] = None
    match_window_seconds: Optional[int] = None


class SettingsIn(BaseModel):
    ftp: Optional[FtpIn] = None
    video_source: Optional[str] = Field(
        default=None, pattern="^(auto|upload|ftp|reolink|demo)$"
    )


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
    name = Path(file.filename or "pos.csv").name
    dest = settings.pos_watch_dir / name
    dest.write_bytes(await file.read())
    matcher = MatcherService(settings)
    ingested = await matcher.ingest_file(db, dest)
    incidents = await matcher.process_pending(db)
    return {"ingested": ingested, "incidents_created": len(incidents), "file": name}


@router.post("/manual-reconcile")
async def manual_reconcile(
    pos_file: UploadFile = File(..., description="Excel/CSV mit Bons"),
    videos: list[UploadFile] | None = File(None, description="Ein oder mehrere Videos"),
    db: AsyncSession = Depends(get_db),
):
    """Manueller Abgleich: Excel/CSV + Video(s) hochladen."""
    settings = get_settings()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.videos_dir.mkdir(parents=True, exist_ok=True)

    pos_name = Path(pos_file.filename or "bons.xlsx").name
    pos_path = settings.uploads_dir / pos_name
    pos_path.write_bytes(await pos_file.read())

    video_paths: list[Path] = []
    for vid in videos or []:
        if not vid.filename:
            continue
        vname = Path(vid.filename).name
        vpath = settings.uploads_dir / vname
        vpath.write_bytes(await vid.read())
        video_paths.append(vpath)

    matcher = MatcherService(settings)
    try:
        result = await matcher.manual_batch(db, pos_path, video_paths)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


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

    tmp = settings.uploads_dir / (video.filename or "clip.mp4")
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


@router.get("/settings")
async def get_settings_api():
    return public_settings()


@router.put("/settings")
async def put_settings_api(body: SettingsIn):
    patch: dict = {}
    if body.ftp is not None:
        patch["ftp"] = {k: v for k, v in body.ftp.model_dump().items() if v is not None}
    if body.video_source is not None:
        patch["video_source"] = body.video_source
    save_runtime(patch)
    return public_settings()


@router.post("/ftp/test")
async def ftp_test():
    cfg = get_ftp_config()
    if not cfg.host:
        raise HTTPException(400, "FTP-Host fehlt")
    try:
        return FtpVideoClient(cfg).test_connection()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"FTP-Verbindung fehlgeschlagen: {exc}") from exc


@router.get("/ftp/videos")
async def ftp_list_videos():
    cfg = get_ftp_config()
    if not cfg.configured:
        raise HTTPException(400, "FTP ist nicht aktiviert oder Host fehlt")
    try:
        videos = FtpVideoClient(cfg).list_videos()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"FTP-Liste fehlgeschlagen: {exc}") from exc
    return [
        {
            "name": v.name,
            "path": v.path,
            "size": v.size,
            "timestamp": v.timestamp.isoformat() if v.timestamp else None,
        }
        for v in videos
    ]


@router.post("/ftp/pull-and-scan", response_model=ScanResult)
async def ftp_pull_and_scan(db: AsyncSession = Depends(get_db)):
    """POS-Ordner einlesen und Videos bevorzugt vom FTP ziehen."""
    cfg = get_ftp_config()
    if not cfg.configured:
        raise HTTPException(400, "FTP ist nicht aktiviert. Bitte unter Einstellungen speichern.")
    # Prefer FTP for this run
    save_runtime({"video_source": "ftp", "ftp": {"enabled": True}})
    settings = get_settings()
    matcher = MatcherService(settings)
    ingested = await matcher.ingest_pos_directory(db)
    # Re-open unprocessed: also requeue latest unprocessed only via ingest.
    # Additionally mark recent unprocessed false → process pending already covers new ones.
    # For existing unprocessed=false only new ones; force pending by resetting processed=False
    # for txs without incidents? Keep simple: only new + currently pending.
    from app.models import Transaction as Tx

    pending = (
        await db.execute(select(Tx).where(Tx.processed.is_(False)))
    ).scalars().all()
    if not pending:
        # Re-queue last 50 for FTP pull when user explicitly asks
        recent = (
            await db.execute(select(Tx).order_by(Tx.timestamp.desc()).limit(50))
        ).scalars().all()
        for row in recent:
            row.processed = False
        await db.commit()
    incidents = await matcher.process_pending(db, limit=50)
    return ScanResult(ingested=ingested, incidents_created=len(incidents))


@router.get("/health")
async def health():
    settings = get_settings()
    ftp = get_ftp_config()
    return {
        "status": "ok",
        "demo_mode": settings.demo_mode,
        "ai_backend": settings.ai_backend,
        "reolink_configured": bool(settings.reolink_host or settings.reolink_rtsp_url),
        "ftp_configured": ftp.configured,
        "ftp_host": ftp.host if ftp.configured else "",
    }
