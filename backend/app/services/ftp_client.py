"""FTP video source: list/download recordings and match them to receipt timestamps."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from ftplib import FTP, error_perm
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".m4v"}

# Common camera / NVR filename timestamps
TS_PATTERNS = [
    re.compile(r"(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})[_T\-]?((?P<h>\d{2})(?P<min>\d{2})(?P<s>\d{2}))?"),
    re.compile(
        r"(?P<d>\d{2})[.\-](?P<m>\d{2})[.\-](?P<y>\d{4})[_\s\-]"
        r"(?P<h>\d{2})[:.\-]?(?P<min>\d{2})[:.\-]?(?P<s>\d{2})?"
    ),
]


@dataclass
class FtpConfig:
    enabled: bool = False
    host: str = ""
    port: int = 21
    user: str = "anonymous"
    password: str = ""
    remote_dir: str = "/"
    passive: bool = True
    timeout: int = 30

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.host)

@dataclass
class RemoteVideo:
    name: str
    path: str
    size: int
    timestamp: Optional[datetime] = None


def parse_timestamp_from_name(name: str) -> Optional[datetime]:
    stem = Path(name).stem
    for pat in TS_PATTERNS:
        m = pat.search(stem)
        if not m:
            continue
        g = m.groupdict()
        try:
            y, mo, d = int(g["y"]), int(g["m"]), int(g["d"])
            h = int(g["h"] or 0)
            mi = int(g.get("min") or 0)
            s = int(g.get("s") or 0)
            return datetime(y, mo, d, h, mi, s)
        except (TypeError, ValueError):
            continue
    return None


class FtpVideoClient:
    def __init__(self, cfg: FtpConfig):
        self.cfg = cfg

    @property
    def configured(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.host)

    def _connect(self) -> FTP:
        ftp = FTP()
        ftp.connect(self.cfg.host, self.cfg.port, timeout=self.cfg.timeout)
        ftp.login(self.cfg.user, self.cfg.password or "")
        ftp.set_pasv(self.cfg.passive)
        if self.cfg.remote_dir and self.cfg.remote_dir != "/":
            ftp.cwd(self.cfg.remote_dir)
        return ftp

    def test_connection(self) -> dict:
        ftp = self._connect()
        try:
            pwd = ftp.pwd()
            names = ftp.nlst()[:20]
            return {"ok": True, "pwd": pwd, "sample_files": names}
        finally:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001
                ftp.close()

    def list_videos(self) -> list[RemoteVideo]:
        ftp = self._connect()
        videos: list[RemoteVideo] = []
        try:
            entries: list[str] = []
            try:
                entries = ftp.nlst()
            except error_perm:
                return []
            for name in entries:
                base = Path(name).name
                if Path(base).suffix.lower() not in VIDEO_EXTS:
                    continue
                size = 0
                try:
                    size = ftp.size(name) or 0
                except Exception:  # noqa: BLE001
                    pass
                videos.append(
                    RemoteVideo(
                        name=base,
                        path=name,
                        size=size,
                        timestamp=parse_timestamp_from_name(base),
                    )
                )
        finally:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001
                ftp.close()
        return videos

    def download(self, remote_path: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        ftp = self._connect()
        try:
            with dest.open("wb") as fh:
                ftp.retrbinary(f"RETR {remote_path}", fh.write)
        finally:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001
                ftp.close()
        return dest

    def find_best_for_timestamp(
        self,
        center: datetime,
        lookback_seconds: int = 5,
        window_seconds: int = 120,
    ) -> Optional[RemoteVideo]:
        """Pick the remote video whose filename timestamp is closest to the receipt."""
        target = center - timedelta(seconds=lookback_seconds)
        videos = [v for v in self.list_videos() if v.timestamp is not None]
        if not videos:
            return None
        best: Optional[RemoteVideo] = None
        best_delta = None
        for v in videos:
            assert v.timestamp is not None
            delta = abs((v.timestamp - target).total_seconds())
            if delta > window_seconds:
                continue
            if best_delta is None or delta < best_delta:
                best = v
                best_delta = delta
        return best

    def download_for_timestamp(
        self,
        center: datetime,
        dest: Path,
        lookback_seconds: int = 5,
        window_seconds: int = 120,
    ) -> Optional[Path]:
        match = self.find_best_for_timestamp(center, lookback_seconds, window_seconds)
        if not match:
            logger.warning("No FTP video matched timestamp %s", center)
            return None
        logger.info("FTP match %s -> %s (Δ ok)", center, match.name)
        return self.download(match.path, dest)
