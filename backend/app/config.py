from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "POS Video Guard"
    data_dir: Path = Path("/data")
    database_url: str = "sqlite+aiosqlite:////data/pos_video_guard.db"

    # POS
    pos_watch_dir: Path = Path("/data/pos")
    pos_file_glob: str = "*.csv"
    pos_timezone: str = "Europe/Berlin"

    # Matching window: seconds before receipt timestamp to analyze
    lookback_seconds: int = 5
    clip_duration_seconds: int = 12
    mismatch_tolerance: int = 0  # allowed item-count difference

    # Reolink
    reolink_host: str = ""
    reolink_user: str = "admin"
    reolink_password: str = ""
    reolink_channel: int = 0
    reolink_rtsp_url: str = ""  # optional override
    reolink_https: bool = False

    # FTP video source (can also be set via Dashboard → runtime_settings.json)
    ftp_enabled: bool = False
    ftp_host: str = ""
    ftp_port: int = 21
    ftp_user: str = ""
    ftp_password: str = ""
    ftp_remote_dir: str = "/"
    ftp_passive: bool = True

    # AI backends: mock | yolo | openai
    ai_backend: str = "yolo"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    yolo_model: str = "yolov8n.pt"
    yolo_confidence: float = 0.35
    # COCO classes treated as "articles" (bags, bottles, cups, etc.)
    yolo_article_classes: str = (
        "bottle,cup,bowl,banana,apple,sandwich,orange,broccoli,"
        "carrot,hot dog,pizza,donut,cake,book,cell phone,laptop,"
        "handbag,backpack,suitcase,toothbrush,remote,scissors,"
        "teddy bear,hair drier,vase,wine glass,fork,knife,spoon"
    )

    # Worker
    scan_interval_seconds: int = 30
    demo_mode: bool = False

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def videos_dir(self) -> Path:
        """Drop-folder / manually uploaded videos awaiting matching."""
        return self.data_dir / "videos"


@lru_cache
def get_settings() -> Settings:
    return Settings()
