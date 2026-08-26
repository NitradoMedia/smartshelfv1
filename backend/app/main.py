import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import incidents, media, ops
from app.config import get_settings
from app.database import init_db
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
    start_scheduler()
    logger.info(
        "Started %s (demo=%s, ai=%s)",
        settings.app_name,
        settings.demo_mode,
        settings.ai_backend,
    )
    yield
    stop_scheduler()


app = FastAPI(title="POS Video Guard", lifespan=lifespan)
app.include_router(incidents.router)
app.include_router(ops.router)
app.include_router(media.router)

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
