from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.database import SessionLocal
from app.services.matcher import MatcherService

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def _scan_job() -> None:
    settings = get_settings()
    matcher = MatcherService(settings)
    async with SessionLocal() as db:
        ingested = await matcher.ingest_pos_directory(db)
        incidents = await matcher.process_pending(db)
        if ingested or incidents:
            logger.info(
                "Scheduled scan: ingested=%s incidents=%s", ingested, len(incidents)
            )


def start_scheduler() -> None:
    settings = get_settings()
    if not scheduler.running:
        scheduler.add_job(
            _scan_job,
            "interval",
            seconds=settings.scan_interval_seconds,
            id="pos_scan",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("Scheduler started (every %ss)", settings.scan_interval_seconds)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
