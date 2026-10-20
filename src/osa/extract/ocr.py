"""Local OCR with RapidOCR (ONNX models shipped inside the wheel; no download, no network).

The engine is created lazily, once per process; importing this module is cheap.
Choice and measurements: docs/adr/0010-pdf-e-ocr-locais.md.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from osa.domain.errors import ExtractionFailed

if TYPE_CHECKING:
    from PIL import Image

# onnxruntime threads per inference session. RLIMIT_CPU counts every thread of the
# worker, so the count is fixed (by default onnxruntime takes every core); measured
# cost in tasks/TASK-105.md ("Correções da review").
OCR_THREADS = 2

_engines: dict[int, Any] = {}


def engine_params(max_side: int) -> dict[str, Any]:
    """RapidOCR parameters: quiet, fixed thread count, and our OCR size as its own limit."""
    threads = max(1, min(OCR_THREADS, os.cpu_count() or 1))
    return {
        "Global.log_level": "critical",
        "Global.max_side_len": max_side,
        "EngineConfig.onnxruntime.intra_op_num_threads": threads,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    }


def choose_orientation[T](
    originals: Sequence[T],
    rotated: Sequence[T],
    flagged: Sequence[int],
    recognize: Callable[[list[T]], Sequence[float]],
) -> list[T]:
    """Keep, for each line the angle classifier turned 180 degrees, the better-read side.

    The classifier sees a line squeezed to 48 x 192 pixels; on a long line (about 30:1)
    it can call an upright line upside down with a score above its threshold (measured:
    0.94 on the Linux raster of the nota fixture). The recognizer reads both sides of
    the flagged lines in one batch and its score decides.
    """
    kept = list(rotated)
    if not flagged:
        return kept
    batch = [originals[i] for i in flagged] + [rotated[i] for i in flagged]
    scores = list(recognize(batch))
    for n, index in enumerate(flagged):
        if scores[n] >= scores[n + len(flagged)]:
            kept[index] = originals[index]
    return kept


def _engine_class() -> Any:
    from rapidocr import RapidOCR
    from rapidocr.ch_ppocr_rec.typings import TextRecInput

    class ArbitratedRapidOCR(RapidOCR):
        """RapidOCR whose 180-degree turns are confirmed by the recognizer."""

        def cls_and_rotate(self, img: list[Any]) -> tuple[list[Any], Any]:
            rotated, cls_res = super().cls_and_rotate(img)
            threshold = self.cfg.Cls.cls_thresh
            flagged = [
                i
                for i, (label, score) in enumerate(cls_res.cls_res or [])
                if "180" in label and score > threshold
            ]

            def recognize(images: list[Any]) -> Sequence[float]:
                rec_model: Any = self._load_rec_model  # untyped library method
                return list(rec_model()(TextRecInput(img=images)).scores)

            kept = choose_orientation(img, rotated, flagged, recognize)
            cls_res.img_list = kept
            return kept, cls_res

    return ArbitratedRapidOCR


def _get_engine(max_side: int) -> Any:
    engine = _engines.get(max_side)
    if engine is None:
        try:
            engine = _engine_class()(params=engine_params(max_side))
        except MemoryError:
            raise
        except Exception as exc:
            raise ExtractionFailed("OCR indisponível") from exc
        _engines[max_side] = engine
    return engine


def _lines(boxes: Any, texts: Any) -> str:
    """Join recognized boxes into text lines in reading order."""
    items: list[tuple[float, float, float, str]] = []
    for box, text in zip(boxes, texts, strict=True):
        ys = [float(p[1]) for p in box]
        xs = [float(p[0]) for p in box]
        items.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(xs), str(text)))
    if not items:
        return ""
    heights = sorted(h for _, h, _, _ in items)
    tolerance = max(heights[len(heights) // 2] * 0.6, 1.0)
    items.sort(key=lambda item: item[0])
    rows: list[list[tuple[float, float, float, str]]] = []
    for item in items:
        if rows and abs(item[0] - rows[-1][-1][0]) <= tolerance:
            rows[-1].append(item)
        else:
            rows.append([item])
    lines = [" ".join(t for *_, t in sorted(row, key=lambda it: it[2])) for row in rows]
    return "\n".join(line.strip() for line in lines if line.strip())


def ocr_image(image: Image.Image, max_side: int) -> str:
    """Text of a PIL image, one line per text row; empty when nothing is recognized.

    ``max_side`` is the longest side the engine works on (``ExtractLimits.max_ocr_side``).
    """
    try:
        import numpy as np

        engine = _get_engine(max_side)
        result = engine(np.asarray(image.convert("RGB")))
        if result.txts is None or result.boxes is None:
            return ""
        return _lines(result.boxes, result.txts)
    except (ExtractionFailed, MemoryError):
        raise
    except Exception as exc:
        raise ExtractionFailed("falha no OCR") from exc
