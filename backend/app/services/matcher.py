"""Core pipeline: ingest POS → extract clip → AI count → create incident if mismatch."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Incident, IncidentStatus, ProcessedFile, Transaction
from app.services.ai_counter import AiCounter
from app.services.ftp_client import FtpVideoClient
from app.services.pos_parser import PosTransaction, parse_pos_file
from app.services.reolink_client import ReolinkClient
from app.services.runtime_settings import get_ftp_config, load_runtime
from app.services.video_match import find_local_video_for_tx, stage_clip
from app.services.video_encode import to_browser_mp4

logger = logging.getLogger(__name__)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class MatcherService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.reolink = ReolinkClient(settings)
        self.ai = AiCounter(settings)
        self.settings.videos_dir.mkdir(parents=True, exist_ok=True)
        self.settings.clips_dir.mkdir(parents=True, exist_ok=True)
        self.settings.uploads_dir.mkdir(parents=True, exist_ok=True)

    async def ingest_pos_directory(self, db: AsyncSession) -> int:
        watch = self.settings.pos_watch_dir
        watch.mkdir(parents=True, exist_ok=True)
        files = sorted(watch.glob(self.settings.pos_file_glob))
        files += sorted(watch.glob("*.json"))
        files += sorted(watch.glob("*.xlsx"))
        files += sorted(watch.glob("*.xlsm"))
        added = 0
        for path in files:
            added += await self.ingest_file(db, path)
        return added

    async def ingest_file(self, db: AsyncSession, path: Path) -> int:
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        existing = await db.scalar(
            select(ProcessedFile).where(ProcessedFile.path == str(path))
        )
        if existing and existing.checksum == checksum:
            return 0

        transactions = parse_pos_file(path)
        added = 0
        for tx in transactions:
            exists = await db.scalar(
                select(Transaction).where(Transaction.external_id == tx.external_id)
            )
            if exists:
                continue
            db.add(
                Transaction(
                    external_id=tx.external_id,
                    timestamp=_aware(tx.timestamp),
                    article_count=tx.article_count,
                    total_amount=tx.total_amount,
                    cashier=tx.cashier,
                    register_id=tx.register_id,
                    raw_payload=tx.raw_payload,
                    source_file=str(path),
                    processed=False,
                )
            )
            added += 1

        if existing:
            existing.checksum = checksum
            existing.processed_at = datetime.now(timezone.utc)
        else:
            db.add(
                ProcessedFile(
                    path=str(path),
                    checksum=checksum,
                    processed_at=datetime.now(timezone.utc),
                )
            )
        await db.commit()
        logger.info("Ingested %s transactions from %s", added, path.name)
        return added

    async def process_pending(self, db: AsyncSession, limit: int = 50) -> list[Incident]:
        result = await db.execute(
            select(Transaction)
            .where(Transaction.processed.is_(False))
            .order_by(Transaction.timestamp.asc())
            .limit(limit)
        )
        pending = list(result.scalars())
        created: list[Incident] = []
        for tx in pending:
            try:
                incident = await self._process_one(db, tx)
                if incident:
                    created.append(incident)
            except Exception:  # noqa: BLE001
                logger.exception("Failed processing transaction %s", tx.external_id)
            tx.processed = True
            await db.commit()
        return created

    async def _resolve_clip(self, tx: Transaction, clip_path: Path) -> str:
        """Obtain a video clip for the transaction. Returns source label."""
        runtime = load_runtime()
        source_pref = str(runtime.get("video_source") or "auto")
        window = int(runtime.get("ftp", {}).get("match_window_seconds") or 180)

        # Local / uploaded videos folder (manual drop)
        if source_pref in {"auto", "upload"}:
            local = find_local_video_for_tx(
                self.settings.videos_dir,
                tx.external_id,
                tx.timestamp.replace(tzinfo=None) if tx.timestamp.tzinfo else tx.timestamp,
                lookback_seconds=self.settings.lookback_seconds,
                window_seconds=window,
            )
            if local:
                stage_clip(local, clip_path)
                return f"upload:{local.name}"

        # FTP
        if source_pref in {"auto", "ftp"}:
            ftp_cfg = get_ftp_config()
            if ftp_cfg.configured:
                client = FtpVideoClient(ftp_cfg)
                try:
                    got = client.download_for_timestamp(
                        center=tx.timestamp.replace(tzinfo=None)
                        if tx.timestamp.tzinfo
                        else tx.timestamp,
                        dest=clip_path,
                        lookback_seconds=self.settings.lookback_seconds,
                        window_seconds=window,
                    )
                    if got:
                        return f"ftp:{got.name}"
                except Exception:  # noqa: BLE001
                    logger.exception("FTP download failed for %s", tx.external_id)

        # Reolink
        if source_pref in {"auto", "reolink"} and self.reolink.configured and not self.settings.demo_mode:
            await self.reolink.extract_clip(
                center=tx.timestamp,
                lookback=self.settings.lookback_seconds,
                duration=self.settings.clip_duration_seconds,
                dest=clip_path,
            )
            return "reolink"

        # Demo / placeholder
        await self._ensure_demo_clip(clip_path, tx)
        return "demo"

    async def _process_one(self, db: AsyncSession, tx: Transaction) -> Incident | None:
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in tx.external_id)
        clip_name = f"{safe_id}_{int(tx.timestamp.timestamp())}.mp4"
        clip_path = self.settings.clips_dir / clip_name
        thumb_path = clip_path.with_suffix(".jpg")

        source = await self._resolve_clip(tx, clip_path)
        try:
            to_browser_mp4(clip_path)
        except Exception:  # noqa: BLE001
            logger.exception("Browser transcode failed for %s", clip_path)
        count = self.ai.count(clip_path)
        self.ai.make_thumbnail(clip_path, thumb_path)

        details = dict(count.details) if isinstance(count.details, dict) else {"raw": count.details}
        details["video_source"] = source

        diff = count.article_count - tx.article_count
        if abs(diff) <= self.settings.mismatch_tolerance:
            logger.info(
                "OK %s: receipt=%s ai=%s source=%s",
                tx.external_id,
                tx.article_count,
                count.article_count,
                source,
            )
            return None

        incident = Incident(
            transaction_id=tx.id,
            external_id=tx.external_id,
            receipt_time=_aware(tx.timestamp),
            receipt_articles=tx.article_count,
            ai_articles=count.article_count,
            difference=diff,
            status=IncidentStatus.open.value,
            clip_path=str(clip_path),
            thumbnail_path=str(thumb_path) if thumb_path.exists() else None,
            ai_backend=count.backend,
            ai_details=json.dumps(details, ensure_ascii=False),
        )
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
        logger.warning(
            "MISMATCH %s: receipt=%s ai=%s diff=%s source=%s",
            tx.external_id,
            tx.article_count,
            count.article_count,
            diff,
            source,
        )
        return incident

    async def _ensure_demo_clip(self, clip_path: Path, tx: Transaction) -> None:
        if clip_path.exists():
            return
        demo_src = Path("/app/demo/sample_checkout.mp4")
        if not demo_src.exists():
            demo_src = Path(__file__).resolve().parents[3] / "demo" / "sample_checkout.mp4"
        if demo_src.exists():
            shutil.copy(demo_src, clip_path)
            to_browser_mp4(clip_path)
            return
        import cv2
        import numpy as np
        import subprocess
        import tempfile

        clip_path.parent.mkdir(parents=True, exist_ok=True)
        # Write raw frames via OpenCV then ffmpeg → H.264 for browsers
        with tempfile.NamedTemporaryFile(suffix=".avi", delete=False) as tmp:
            raw = Path(tmp.name)
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        writer = cv2.VideoWriter(str(raw), fourcc, 10.0, (640, 360))
        for i in range(40):
            frame = np.zeros((360, 640, 3), dtype=np.uint8)
            frame[:] = (28, 36, 42)
            for n in range(max(tx.article_count, 1)):
                x = 80 + (n % 5) * 100
                y = 100 + (n // 5) * 80
                color = (60 + n * 20, 140, 200 - n * 10)
                cv2.rectangle(frame, (x, y), (x + 70, y + 50), color, -1)
            label = f"BON {tx.external_id}  {tx.timestamp.strftime('%H:%M:%S')}"
            cv2.putText(
                frame, label, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (230, 230, 230), 2
            )
            cv2.putText(
                frame,
                f"Frame {i}",
                (20, 330),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (160, 160, 160),
                1,
            )
            writer.write(frame)
        writer.release()
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(raw),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-profile:v",
                    "baseline",
                    "-movflags",
                    "+faststart",
                    "-an",
                    str(clip_path),
                ],
                check=True,
                capture_output=True,
            )
        finally:
            raw.unlink(missing_ok=True)
    async def manual_batch(
        self,
        db: AsyncSession,
        pos_path: Path,
        video_paths: list[Path],
    ) -> dict:
        """Ingest Excel/CSV and pair with uploaded videos, then process."""
        staged: list[str] = []
        for vp in video_paths:
            dest = self.settings.videos_dir / vp.name
            stage_clip(vp, dest)
            staged.append(dest.name)

        pos_dest = self.settings.pos_watch_dir / pos_path.name
        stage_clip(pos_path, pos_dest)

        txs = parse_pos_file(pos_dest)

        # 1 video + 1 Bon → rename/copy to bon-id for reliable pairing
        if len(video_paths) == 1 and len(txs) == 1:
            dest = self.settings.videos_dir / f"{txs[0].external_id}{video_paths[0].suffix}"
            stage_clip(video_paths[0], dest)
            staged = [dest.name]

        ingested = await self.ingest_file(db, pos_dest)
        if video_paths:
            for tx in txs:
                row = await db.scalar(
                    select(Transaction).where(Transaction.external_id == tx.external_id)
                )
                if row:
                    row.processed = False
            await db.commit()

        incidents = await self.process_pending(db, limit=max(50, len(txs) + 5))
        return {
            "ingested": ingested,
            "videos_staged": staged,
            "transactions_in_file": len(txs),
            "incidents_created": len(incidents),
            "incident_ids": [i.id for i in incidents],
        }

    async def reprocess_with_upload(
        self, db: AsyncSession, tx: PosTransaction, video_path: Path
    ) -> Incident | None:
        exists = await db.scalar(
            select(Transaction).where(Transaction.external_id == tx.external_id)
        )
        if not exists:
            exists = Transaction(
                external_id=tx.external_id,
                timestamp=_aware(tx.timestamp),
                article_count=tx.article_count,
                total_amount=tx.total_amount,
                cashier=tx.cashier,
                register_id=tx.register_id,
                raw_payload=tx.raw_payload,
                source_file="upload",
                processed=True,
            )
            db.add(exists)
            await db.commit()
            await db.refresh(exists)

        dest = self.settings.clips_dir / f"upload_{tx.external_id}.mp4"
        shutil.copy(video_path, dest)
        to_browser_mp4(dest)
        count = self.ai.count(dest)
        thumb = dest.with_suffix(".jpg")
        self.ai.make_thumbnail(dest, thumb)
        diff = count.article_count - tx.article_count
        if abs(diff) <= self.settings.mismatch_tolerance:
            return None
        incident = Incident(
            transaction_id=exists.id,
            external_id=tx.external_id,
            receipt_time=_aware(tx.timestamp),
            receipt_articles=tx.article_count,
            ai_articles=count.article_count,
            difference=diff,
            status=IncidentStatus.open.value,
            clip_path=str(dest),
            thumbnail_path=str(thumb) if thumb.exists() else None,
            ai_backend=count.backend,
            ai_details=json.dumps(
                {**(count.details if isinstance(count.details, dict) else {}), "video_source": "manual"},
                ensure_ascii=False,
            ),
        )
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
        return incident
