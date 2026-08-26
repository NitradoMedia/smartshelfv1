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
from app.services.pos_parser import PosTransaction, parse_pos_file
from app.services.reolink_client import ReolinkClient

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

    async def ingest_pos_directory(self, db: AsyncSession) -> int:
        watch = self.settings.pos_watch_dir
        watch.mkdir(parents=True, exist_ok=True)
        files = sorted(watch.glob(self.settings.pos_file_glob))
        # also json
        files += sorted(watch.glob("*.json"))
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

    async def process_pending(self, db: AsyncSession, limit: int = 20) -> list[Incident]:
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

    async def _process_one(self, db: AsyncSession, tx: Transaction) -> Incident | None:
        clip_name = f"{tx.external_id}_{int(tx.timestamp.timestamp())}.mp4"
        clip_path = self.settings.clips_dir / clip_name
        thumb_path = clip_path.with_suffix(".jpg")

        if self.settings.demo_mode or not self.reolink.configured:
            await self._ensure_demo_clip(clip_path, tx)
        else:
            await self.reolink.extract_clip(
                center=tx.timestamp,
                lookback=self.settings.lookback_seconds,
                duration=self.settings.clip_duration_seconds,
                dest=clip_path,
            )

        count = self.ai.count(clip_path)
        self.ai.make_thumbnail(clip_path, thumb_path)

        diff = count.article_count - tx.article_count
        if abs(diff) <= self.settings.mismatch_tolerance:
            # optional: store matched audits — skip for noise reduction
            logger.info(
                "OK %s: receipt=%s ai=%s",
                tx.external_id,
                tx.article_count,
                count.article_count,
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
            ai_details=json.dumps(count.details, ensure_ascii=False),
        )
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
        logger.warning(
            "MISMATCH %s: receipt=%s ai=%s diff=%s",
            tx.external_id,
            tx.article_count,
            count.article_count,
            diff,
        )
        return incident

    async def _ensure_demo_clip(self, clip_path: Path, tx: Transaction) -> None:
        """Create a synthetic clip or copy demo asset when no camera is available."""
        if clip_path.exists():
            return
        demo_src = Path("/app/demo/sample_checkout.mp4")
        if demo_src.exists():
            shutil.copy(demo_src, clip_path)
            return
        # Generate a short placeholder video with OpenCV
        import cv2
        import numpy as np

        clip_path.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(clip_path), fourcc, 10.0, (640, 360))
        for i in range(40):
            frame = np.zeros((360, 640, 3), dtype=np.uint8)
            frame[:] = (28, 36, 42)
            # Draw fake "articles" proportional to receipt count (so mock/yolo demos vary)
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

    async def reprocess_with_upload(
        self, db: AsyncSession, tx: PosTransaction, video_path: Path
    ) -> Incident | None:
        """Manual path: user uploads POS row + matching video clip."""
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
            ai_details=json.dumps(count.details, ensure_ascii=False),
        )
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
        return incident
