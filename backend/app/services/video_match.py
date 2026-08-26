"""Match local/uploaded video files to POS transactions by bon-id or timestamp."""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.services.ftp_client import VIDEO_EXTS, parse_timestamp_from_name


def list_local_videos(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )


def find_local_video_for_tx(
    folder: Path,
    external_id: str,
    timestamp: datetime,
    lookback_seconds: int = 5,
    window_seconds: int = 180,
) -> Optional[Path]:
    videos = list_local_videos(folder)
    if not videos:
        return None

    # 1) Exact / partial bon-id in filename
    eid = re.sub(r"[^a-zA-Z0-9\-]", "", external_id).lower()
    for p in videos:
        stem = re.sub(r"[^a-zA-Z0-9\-]", "", p.stem).lower()
        if eid and eid in stem:
            return p

    # 2) Closest timestamp in filename
    target = timestamp - timedelta(seconds=lookback_seconds)
    best: Optional[Path] = None
    best_delta: Optional[float] = None
    for p in videos:
        ts = parse_timestamp_from_name(p.name)
        if ts is None:
            continue
        delta = abs((ts - target).total_seconds())
        if delta > window_seconds:
            continue
        if best_delta is None or delta < best_delta:
            best = p
            best_delta = delta
    return best


def stage_clip(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        shutil.copy(src, dest)
    return dest
