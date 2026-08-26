import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import incidents, media, network, ops, preview, record
from app.config import get_settings
from app.database import init_db
from app.services.rtsp_preview import previews
from app.services.rtsp_recorder import recorder
from app.worker import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("pos_video_guard")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    await init_db()
    if settings.ai_backend.lower() == "yolo":
        try:
            from app.services.ai_counter import AiCounter

            AiCounter(settings).warmup()
        except Exception:  # noqa: BLE001
            logger.exception("YOLO warmup failed – first request may download weights")
    start_scheduler()
    logger.info(
        "Started %s (demo=%s, ai=%s, model=%s)",
        settings.app_name,
        settings.demo_mode,
        settings.ai_backend,
        settings.yolo_model,
    )
    yield
    recorder.stop_all()
    previews.stop_all()
    stop_scheduler()


app = FastAPI(title="POS Video Guard", lifespan=lifespan)
app.include_router(incidents.router)
app.include_router(ops.router)
app.include_router(media.router)
app.include_router(record.router)
app.include_router(preview.router)
app.include_router(network.router)

STATIC = Path(__file__).resolve().parent.parent.parent / "frontend" / "static"
# In Docker the frontend is copied next to app
if not STATIC.exists():
    STATIC = Path("/app/frontend/static")

if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
async def index():
    index_path = STATIC.parent / "index.html"
    if not index_path.exists():
        index_path = Path("/app/frontend/index.html")
    return FileResponse(index_path)
