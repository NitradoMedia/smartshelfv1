"""Reolink camera client: login, search recordings, download clips, RTSP fallback."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class ReolinkClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._token: Optional[str] = None
        scheme = "https" if settings.reolink_https else "http"
        self.base = f"{scheme}://{settings.reolink_host}"

    @property
    def configured(self) -> bool:
        return bool(self.settings.reolink_host or self.settings.reolink_rtsp_url)

    async def login(self) -> str:
        if not self.settings.reolink_host:
            raise RuntimeError("REOLINK_HOST not configured")
        payload = [
            {
                "cmd": "Login",
                "param": {
                    "User": {
                        "userName": self.settings.reolink_user,
                        "password": self.settings.reolink_password,
                    }
                },
            }
        ]
        async with httpx.AsyncClient(verify=False, timeout=20) as client:
            resp = await client.post(f"{self.base}/api.cgi?cmd=Login", json=payload)
            resp.raise_for_status()
            data = resp.json()
            token = data[0]["value"]["Token"]["name"]
            self._token = token
            return token

    async def _ensure_token(self) -> str:
        if not self._token:
            return await self.login()
        return self._token

    async def search_recordings(
        self, start: datetime, end: datetime
    ) -> list[dict]:
        token = await self._ensure_token()
        payload = [
            {
                "cmd": "Search",
                "action": 0,
                "param": {
                    "Search": {
                        "channel": self.settings.reolink_channel,
                        "onlyStatus": 0,
                        "streamType": "main",
                        "StartTime": _reolink_time(start),
                        "EndTime": _reolink_time(end),
                    }
                },
            }
        ]
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.post(
                f"{self.base}/api.cgi?cmd=Search&token={token}", json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            try:
                return data[0]["value"]["SearchResult"].get("File", []) or []
            except (KeyError, IndexError, TypeError):
                logger.warning("No recordings found for %s – %s", start, end)
                return []

    async def download_recording(self, file_name: str, dest: Path) -> Path:
        token = await self._ensure_token()
        url = (
            f"{self.base}/cgi-bin/api.cgi?cmd=Download"
            f"&source={file_name}&output={dest.name}&token={token}"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(verify=False, timeout=120) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with dest.open("wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
        return dest

    def rtsp_url(self) -> str:
        if self.settings.reolink_rtsp_url:
            return self.settings.reolink_rtsp_url
        user = self.settings.reolink_user
        pw = self.settings.reolink_password
        host = self.settings.reolink_host
        ch = self.settings.reolink_channel
        # Reolink typical RTSP path
        return f"rtsp://{user}:{pw}@{host}:554/h264Preview_{ch + 1:02d}_main"

    async def extract_clip(
        self,
        center: datetime,
        lookback: int,
        duration: int,
        dest: Path,
    ) -> Path:
        """Extract a clip around a transaction timestamp.

        Prefer downloading NVR segment; fall back to RTSP live-relative only when
        demo/offline. For historical clips we use ffmpeg with RTSP if the camera
        supports playback URLs, otherwise download Search results.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        start = center - timedelta(seconds=lookback)
        end = start + timedelta(seconds=duration)

        if self.settings.reolink_host:
            try:
                files = await self.search_recordings(start - timedelta(minutes=1), end + timedelta(minutes=1))
                if files:
                    raw = dest.with_suffix(".mp4.download")
                    await self.download_recording(files[0]["name"], raw)
                    await asyncio.to_thread(
                        _ffmpeg_trim, raw, dest, lookback_offset=0, duration=duration
                    )
                    raw.unlink(missing_ok=True)
                    return dest
            except Exception as exc:  # noqa: BLE001
                logger.warning("Reolink API clip failed, trying RTSP: %s", exc)

        # Live RTSP grab (useful for near-realtime / demo; historical needs NVR)
        await asyncio.to_thread(
            _ffmpeg_rtsp_grab, self.rtsp_url(), dest, duration
        )
        return dest


def _reolink_time(dt: datetime) -> dict:
    return {
        "year": dt.year,
        "mon": dt.month,
        "day": dt.day,
        "hour": dt.hour,
        "min": dt.minute,
        "sec": dt.second,
    }


def _ffmpeg_trim(source: Path, dest: Path, lookback_offset: int, duration: int) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(lookback_offset),
        "-i",
        str(source),
        "-t",
        str(duration),
        "-c",
        "copy",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _ffmpeg_rtsp_grab(rtsp: str, dest: Path, duration: int) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp,
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-an",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
