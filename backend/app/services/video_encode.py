"""Ensure clips are browser-playable H.264 MP4 (yuv420p + faststart)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def probe_video_codec(path: Path) -> str | None:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            text=True,
        ).strip()
        return out or None
    except Exception:  # noqa: BLE001
        return None


def is_browser_friendly(path: Path) -> bool:
    codec = (probe_video_codec(path) or "").lower()
    return codec in {"h264", "avc1", "avc"}


def to_browser_mp4(src: Path, dest: Path | None = None) -> Path:
    """Transcode (or remux when already H.264) to browser-safe MP4.

    Returns path to the playable file (dest or src when in-place).
    """
    dest = dest or src
    if not src.exists() or src.stat().st_size == 0:
        raise FileNotFoundError(f"Video missing or empty: {src}")

    # Already good and same path → nothing to do
    if dest == src and is_browser_friendly(src):
        # Still ensure moov atom is at front for streaming
        _faststart_inplace(src)
        return src

    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "baseline",
        "-level",
        "3.0",
        "-movflags",
        "+faststart",
        "-an",
        str(tmp_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        tmp_path.unlink(missing_ok=True)
        err = (exc.stderr or b"").decode("utf-8", errors="ignore")[-500:]
        raise RuntimeError(f"ffmpeg transcode failed: {err}") from exc

    if dest == src:
        tmp_path.replace(src)
        return src
    shutil.move(str(tmp_path), str(dest))
    return dest


def _faststart_inplace(path: Path) -> None:
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(tmp_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        tmp_path.replace(path)
    except Exception:  # noqa: BLE001
        tmp_path.unlink(missing_ok=True)
        logger.debug("faststart remux skipped for %s", path, exc_info=True)
