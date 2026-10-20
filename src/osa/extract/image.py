"""Standalone images (PNG, JPEG, HEIC): bounded decode, PNG for the model, OCR text."""

from __future__ import annotations

import io
import warnings
from pathlib import Path

from PIL import Image, ImageOps

from osa.domain.errors import ExtractionFailed
from osa.extract.model import ExtractedContent, ExtractLimits, PageImage
from osa.extract.ocr import ocr_image

_FORMATS = ["PNG", "JPEG", "HEIF"]
_heif_registered = False


def _register_heif() -> None:
    global _heif_registered
    if not _heif_registered:
        from pi_heif import register_heif_opener

        register_heif_opener()
        _heif_registered = True


def to_rgb(image: Image.Image) -> Image.Image:
    """RGB copy; transparent areas are composited over white, never black."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        base = Image.new("RGB", rgba.size, "white")
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    return image.convert("RGB")


def page_image(image: Image.Image, page: int, max_side: int) -> PageImage:
    """PNG of ``image`` with the longest side at most ``max_side`` pixels."""
    small = image.copy()
    small.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    small.save(buffer, format="PNG")
    return PageImage(data=buffer.getvalue(), page=page, width=small.width, height=small.height)


def load_image(path: Path, limits: ExtractLimits) -> Image.Image:
    """Decode ``path`` to RGB, upright, with the longest side within ``max_ocr_side``.

    The pixel count is checked from the header, before any pixel is decoded or
    converted; Pillow's decompression-bomb warning is treated as an error.
    """
    _register_heif()
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = limits.max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path, formats=_FORMATS) as opened:
                width, height = opened.size
                if width < 1 or height < 1 or width * height > limits.max_pixels:
                    raise ExtractionFailed("imagem grande demais")
                opened.draft("RGB", (limits.max_ocr_side, limits.max_ocr_side))
                opened.load()
                upright = ImageOps.exif_transpose(opened)
                rgb = to_rgb(upright)
    except (ExtractionFailed, MemoryError):
        raise
    except Exception as exc:
        raise ExtractionFailed("imagem inválida ou grande demais") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous
    rgb.thumbnail((limits.max_ocr_side, limits.max_ocr_side), Image.Resampling.LANCZOS)
    return rgb


def extract_image(path: Path, limits: ExtractLimits) -> ExtractedContent:
    image = load_image(path, limits)
    text = ocr_image(image, limits.max_ocr_side).strip()
    truncated = len(text) > limits.max_text_chars
    return ExtractedContent(
        text=text[: limits.max_text_chars],
        images=(page_image(image, 1, limits.max_image_side),),
        pages_total=1,
        truncated=truncated,
        method="image",
    )
