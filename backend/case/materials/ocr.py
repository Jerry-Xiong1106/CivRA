"""Switchable image-to-text engines. Loading this module never downloads a model."""

from __future__ import annotations

import io
import threading
from importlib.metadata import version
from typing import Callable, Literal, Protocol

from pydantic import Field

from ..models import ContractModel


class OcrLine(ContractModel):
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    polygon: list[list[float]] | None = None


class OcrPage(ContractModel):
    engine: str
    engine_version: str
    model_version: str
    lines: list[OcrLine]
    warnings: list[str] = Field(default_factory=list)


class OcrEngine(Protocol):
    def recognize(self, image_bytes: bytes) -> OcrPage: ...


class PaddleOcrConfig(ContractModel):
    # Version selection is explicit; application configuration must pin it.
    ocr_version: Literal["PP-OCRv5", "PP-OCRv6"]
    device: Literal["cpu"] = "cpu"
    detection_model_name: str | None = None
    recognition_model_name: str | None = None
    detection_model_dir: str | None = None
    recognition_model_dir: str | None = None
    cpu_threads: int = Field(default=4, ge=1, le=64)
    enable_mkldnn: bool = False


class PaddleOcrEngine:
    def __init__(self, config: PaddleOcrConfig, *, client=None):
        self.config = config
        self._client = client
        self._lock = threading.Lock()

    def _load(self):
        if self._client is None:
            try:
                from paddleocr import PaddleOCR
            except ImportError as exc:
                raise RuntimeError("PaddleOCR is not installed. Install requirements-ocr.txt in the OCR environment.") from exc
            options = dict(ocr_version=self.config.ocr_version, device="cpu", cpu_threads=self.config.cpu_threads,
                           enable_mkldnn=self.config.enable_mkldnn,
                           use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)
            for field, option in [("detection_model_name", "text_detection_model_name"),
                                  ("recognition_model_name", "text_recognition_model_name"),
                                  ("detection_model_dir", "text_detection_model_dir"),
                                  ("recognition_model_dir", "text_recognition_model_dir")]:
                if getattr(self.config, field):
                    options[option] = getattr(self.config, field)
            self._client = PaddleOCR(**options)
        return self._client

    def recognize(self, image_bytes: bytes) -> OcrPage:
        import numpy as np
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            # PaddleOCR numpy image inputs follow OpenCV BGR order.
            pixels = np.asarray(ImageOps.exif_transpose(image).convert("RGB"))[:, :, ::-1].copy()
        with self._lock:
            prediction = list(self._load().predict(pixels))
        lines = []
        for page in prediction:
            raw = page.json if hasattr(page, "json") else page
            if callable(raw):
                raw = raw()
            if not isinstance(raw, dict):
                raise RuntimeError("unsupported PaddleOCR result; expected a v3 JSON object")
            raw = raw.get("res", raw)
            texts, scores = raw.get("rec_texts", []), raw.get("rec_scores", [])
            polygons = raw.get("rec_polys", raw.get("dt_polys", []))
            if len(texts) != len(scores) or len(polygons) != len(texts):
                raise RuntimeError("PaddleOCR text/score/polygon counts differ")
            for text, score, polygon in zip(texts, scores, polygons):
                if text.strip():
                    points = polygon.tolist() if hasattr(polygon, "tolist") else polygon
                    lines.append(OcrLine(text=text, confidence=float(score), polygon=points))
        try:
            package_version = version("paddleocr")
        except Exception:
            package_version = "injected-test-client"
        return OcrPage(engine="paddleocr", engine_version=package_version,
                       model_version=":".join([self.config.ocr_version,
                           self.config.detection_model_name or self.config.detection_model_dir or "default-det",
                           self.config.recognition_model_name or self.config.recognition_model_dir or "default-rec"]), lines=lines)


class OcrEngineRegistry:
    """Register another adapter with the same Protocol to switch engine without parser changes."""

    def __init__(self):
        self._factories: dict[str, Callable[[dict], OcrEngine]] = {
            "paddleocr": lambda options: PaddleOcrEngine(PaddleOcrConfig.model_validate(options)),
        }

    def register(self, name: str, factory: Callable[[dict], OcrEngine]):
        if not name or name in self._factories:
            raise ValueError("OCR engine name is empty or already registered")
        self._factories[name] = factory

    def create(self, name: str, options: dict) -> OcrEngine:
        if name not in self._factories:
            raise ValueError(f"unknown OCR engine: {name}")
        return self._factories[name](options)


def default_ocr_engine() -> OcrEngine:
    """Pinned local default; construction is lazy and does not download models."""
    return OcrEngineRegistry().create("paddleocr", {
        "ocr_version": "PP-OCRv6", "device": "cpu", "cpu_threads": 4,
        "detection_model_name": "PP-OCRv6_small_det",
        "recognition_model_name": "PP-OCRv6_small_rec",
    })
