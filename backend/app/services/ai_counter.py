"""AI article counters: YOLO (local), OpenAI Vision, and mock for demos."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class CountResult:
    article_count: int
    details: dict
    backend: str


class AiCounter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._yolo = None

    def count(self, video_path: Path) -> CountResult:
        backend = self.settings.ai_backend.lower()
        if backend == "openai":
            return self._count_openai(video_path)
        if backend == "mock":
            return self._count_mock(video_path)
        return self._count_yolo(video_path)

    def _load_yolo(self):
        if self._yolo is None:
            from ultralytics import YOLO

            model_name = self.settings.yolo_model
            model_path = Path(model_name)
            if not model_path.is_file():
                cached = self.settings.data_dir / "models" / Path(model_name).name
                if cached.is_file():
                    model_path = cached
                else:
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    # Ultralytics downloads by name into CWD; prefer data/models
                    model_path = Path(model_name)
            logger.info("Loading YOLO model: %s", model_path)
            self._yolo = YOLO(str(model_path))
            # Persist downloaded weights under data/models when possible
            try:
                src = Path(self._yolo.ckpt_path) if hasattr(self._yolo, "ckpt_path") else None
                dest = self.settings.data_dir / "models" / Path(model_name).name
                if src and src.is_file() and not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(src.read_bytes())
            except Exception:  # noqa: BLE001
                logger.debug("Could not cache YOLO weights", exc_info=True)
        return self._yolo

    def warmup(self) -> None:
        if self.settings.ai_backend.lower() != "yolo":
            return
        model = self._load_yolo()
        import numpy as np

        blank = np.zeros((320, 320, 3), dtype=np.uint8)
        model.predict(blank, conf=self.settings.yolo_confidence, verbose=False)
        logger.info("YOLO warmup complete (%s)", self.settings.yolo_model)

    def _sample_frames(self, video_path: Path, max_frames: int = 8) -> list[np.ndarray]:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        frames: list[np.ndarray] = []
        if total <= 0:
            ok, frame = cap.read()
            if ok:
                frames.append(frame)
            cap.release()
            return frames
        indices = np.linspace(0, max(total - 1, 0), num=min(max_frames, total), dtype=int)
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, frame = cap.read()
            if ok:
                frames.append(frame)
        cap.release()
        return frames

    def _count_yolo(self, video_path: Path) -> CountResult:
        try:
            model = self._load_yolo()
        except ImportError as exc:
            raise RuntimeError(
                "AI_BACKEND=yolo requires ultralytics. "
                "Install with: pip install ultralytics"
            ) from exc
        allowed = {
            c.strip().lower()
            for c in self.settings.yolo_article_classes.split(",")
            if c.strip()
        }
        frames = self._sample_frames(video_path)
        per_frame: list[dict] = []
        counts: list[int] = []
        for frame in frames:
            results = model.predict(
                frame, conf=self.settings.yolo_confidence, verbose=False
            )
            labels: list[str] = []
            for r in results:
                names = r.names
                if r.boxes is None:
                    continue
                for box in r.boxes:
                    cls_id = int(box.cls.item())
                    name = str(names.get(cls_id, cls_id)).lower()
                    if name in allowed:
                        labels.append(name)
            counts.append(len(labels))
            per_frame.append({"count": len(labels), "labels": labels})

        # Use max across frames — items may be occluded in some samples
        article_count = max(counts) if counts else 0
        return CountResult(
            article_count=article_count,
            details={"frames": per_frame, "method": "yolo_max_frames"},
            backend="yolo",
        )

    def _count_openai(self, video_path: Path) -> CountResult:
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY required for ai_backend=openai")
        from openai import OpenAI
        import base64
        from io import BytesIO
        from PIL import Image

        client = OpenAI(api_key=self.settings.openai_api_key)
        frames = self._sample_frames(video_path, max_frames=4)
        content: list[dict] = [
            {
                "type": "text",
                "text": (
                    "Du analysierst Videoframes eines Kassenbereichs. "
                    "Zähle die sichtbaren Verkaufsartikel (Waren, die der Kunde "
                    "auf das Band / an die Kasse legt). Ignoriere Personen, "
                    "Kasse, Geld und Inventar im Hintergrund. "
                    'Antworte NUR als JSON: {"article_count": <int>, "note": "..."}'
                ),
            }
        ]
        for frame in frames:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            img.thumbnail((1024, 1024))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )

        resp = client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[{"role": "user", "content": content}],
            temperature=0,
        )
        text = resp.choices[0].message.content or "{}"
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            data = json.loads(text[start:end])
            count = int(data.get("article_count", 0))
        except Exception:  # noqa: BLE001
            logger.exception("Failed to parse OpenAI response: %s", text)
            count = 0
            data = {"raw": text}
        return CountResult(article_count=count, details=data, backend="openai")

    def _count_mock(self, video_path: Path) -> CountResult:
        """Deterministic mock based on filename hash — for demos without camera/GPU."""
        seed = sum(video_path.name.encode()) % 7
        # Slightly biased so demos produce some mismatches
        count = 3 + seed
        return CountResult(
            article_count=count,
            details={"note": "mock backend", "seed": seed},
            backend="mock",
        )

    def make_thumbnail(self, video_path: Path, dest: Path) -> Optional[Path]:
        frames = self._sample_frames(video_path, max_frames=1)
        if not frames:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(dest), frames[0])
        return dest
