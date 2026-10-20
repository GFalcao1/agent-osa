"""PDF extraction with pypdfium2: text layer per page, images and OCR for the rest.

The decision is per page:

- text page: enough alphanumeric text (``min_chars_per_page``) and no image covering
  most of the page; it contributes its text layer;
- text over image: a page covered by an image (letterhead background, searchable
  scan) whose text layer is large (``min_chars_text_over_image``); it contributes
  its text layer and is also rendered for the vision model (up to
  ``max_image_pages``), without OCR, so the text is neither duplicated nor
  truncated by OCR noise;
- image page: anything else (no text, or a scan whose only text is a scanner
  stamp); rendered for the vision model and run through local OCR (up to
  ``max_ocr_pages``), so owner evidence printed inside a scan still becomes text.
  A small text layer is kept before the OCR text.

``method`` says where the text came from: ``pdf_text`` when every page gave its
text layer (text-over-image pages included, which may also bring images),
``pdf_scanned`` when every page is an image page, ``pdf_mixed`` otherwise.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

from osa.domain.errors import ExtractionFailed
from osa.extract.image import page_image, to_rgb
from osa.extract.model import ExtractedContent, ExtractLimits, Method, PageImage, TextBudget
from osa.extract.ocr import ocr_image

_MIN_SCALE = 0.25  # below this the page is too large to render sensibly
# A page whose image objects cover at least this share of it is a scanned page.
IMAGE_COVERAGE = 0.5
# Objects visited per page and form nesting; past either the page counts as covered.
_MAX_PAGE_OBJECTS = 20_000
_MAX_FORM_DEPTH = 8
_BLANK_LINES = re.compile(r"\n{3,}")


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return _BLANK_LINES.sub("\n\n", text).strip()


def _alnum(text: str) -> int:
    return sum(c.isalnum() for c in text)


class _TooComplex(Exception):
    """The page has more objects or deeper forms than the walk is allowed to visit."""


def _image_rects(
    page: Any, form: Any, matrices: tuple[Any, ...], budget: list[int]
) -> list[tuple[float, float, float, float]]:
    """Bounds of every image object, in page coordinates, descending into Form XObjects.

    PDFium gives the bounds of an object inside a form in that form's space (the
    form's own /Matrix already applied); each enclosing form object's matrix (the
    transformation at its ``Do``) maps it one level up. Measured with scaled,
    nested and /Matrix forms (tests).
    """
    if len(matrices) > _MAX_FORM_DEPTH:
        raise _TooComplex
    rects: list[tuple[float, float, float, float]] = []
    for obj in page.get_objects(max_depth=1, form=form):
        budget[0] -= 1
        if budget[0] < 0:
            raise _TooComplex
        if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
            rect = tuple(float(v) for v in obj.get_bounds())
            for matrix in matrices:  # innermost first
                rect = tuple(float(v) for v in matrix.on_rect(*rect))
            rects.append(rect)  # type: ignore[arg-type]
        elif obj.type == pdfium_c.FPDF_PAGEOBJ_FORM:
            rects.extend(_image_rects(page, obj, (obj.get_matrix(), *matrices), budget))
    return rects


def image_coverage(page: Any) -> float:
    """Share of the page area covered by image objects (bounds clipped to the page).

    Images inside Form XObjects count, transformed to page coordinates. Overlapping
    images are summed, so this can overestimate; capped at 1.0. A page with more
    objects or deeper forms than the walk visits counts as covered (conservative:
    it becomes an image page and goes through OCR).
    """
    left, bottom, right, top = (float(v) for v in page.get_bbox())
    area = (right - left) * (top - bottom)
    if not (math.isfinite(area) and area > 0):
        return 0.0
    try:
        rects = _image_rects(page, None, (), [_MAX_PAGE_OBJECTS])
    except _TooComplex:
        return 1.0
    covered = 0.0
    for x0, y0, x1, y1 in rects:
        width = min(x1, right) - max(x0, left)
        height = min(y1, top) - max(y0, bottom)
        if width > 0 and height > 0:
            covered += width * height
    return min(covered / area, 1.0)


def _page_text(page: Any) -> str:
    textpage = page.get_textpage()
    try:
        return _clean(textpage.get_text_range())
    finally:
        textpage.close()


def _scale_for(width: float, height: float, limits: ExtractLimits) -> float:
    """Render scale for OCR, lowered so the raster never exceeds ``max_render_pixels``."""
    if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
        raise ExtractionFailed("página com tamanho inválido")
    scale = limits.render_dpi / 72
    pixels = width * scale * height * scale
    if pixels > limits.max_render_pixels:
        scale *= math.sqrt(limits.max_render_pixels / pixels)
    if scale < _MIN_SCALE:
        raise ExtractionFailed("página grande demais para renderizar")
    return scale


def _render(page: Any, scale: float) -> Image.Image:
    bitmap = page.render(scale=scale, may_draw_forms=False)
    try:
        return to_rgb(bitmap.to_pil())
    finally:
        bitmap.close()


def _render_page(document: Any, index: int, page: Any, limits: ExtractLimits) -> Image.Image:
    width, height = document.get_page_size(index)
    return _render(page, _scale_for(width, height, limits))


def extract_pdf(path: Path, limits: ExtractLimits) -> ExtractedContent:
    try:
        document = pdfium.PdfDocument(str(path))
    except MemoryError:
        raise
    except Exception as exc:
        raise ExtractionFailed("PDF inválido ou protegido por senha") from exc
    try:
        return _extract(document, limits)
    except (ExtractionFailed, MemoryError):
        raise
    except Exception as exc:
        raise ExtractionFailed("falha ao ler o PDF") from exc
    finally:
        document.close()


def _extract(document: Any, limits: ExtractLimits) -> ExtractedContent:
    pages_total = len(document)
    if pages_total < 1:
        raise ExtractionFailed("PDF sem páginas")
    budget = TextBudget(limits.max_text_chars)
    images: list[PageImage] = []
    text_pages = 0
    image_pages = 0
    ocr_done = 0
    skipped = False
    for index in range(pages_total):
        if budget.full and (len(images) >= limits.max_image_pages or text_pages > 0):
            skipped = True
            break
        page = document[index]
        try:
            text = _page_text(page)
            chars = _alnum(text)
            covered = image_coverage(page) >= IMAGE_COVERAGE
            text_layer = chars >= (
                limits.min_chars_text_over_image if covered else limits.min_chars_per_page
            )
            if text_layer:
                text_pages += 1
                if budget.full:
                    skipped = True
                else:
                    budget.add(text, "\n\n")
                if covered and len(images) < limits.max_image_pages:
                    raster = _render_page(document, index, page, limits)
                    images.append(page_image(raster, index + 1, limits.max_image_side))
                continue
            image_pages += 1
            if covered and text:
                budget.add(text, "\n\n")  # scanner stamp or small text layer of a scan
            want_image = len(images) < limits.max_image_pages
            want_ocr = not budget.full and ocr_done < limits.max_ocr_pages
            if not (want_image or want_ocr):
                skipped = True
                continue
            raster = _render_page(document, index, page, limits)
            if want_image:
                images.append(page_image(raster, index + 1, limits.max_image_side))
            if want_ocr:
                ocr_done += 1
                budget.add(ocr_image(raster, limits.max_ocr_side).strip(), "\n\n")
            else:
                skipped = True
        finally:
            page.close()
    method: Method
    if image_pages == 0:
        method = "pdf_text"
    elif text_pages == 0:
        method = "pdf_scanned"
    else:
        method = "pdf_mixed"
    text_out = budget.text()
    if not text_out and not images:
        raise ExtractionFailed("PDF sem conteúdo extraível")
    return ExtractedContent(
        text=text_out,
        images=tuple(images),
        pages_total=pages_total,
        truncated=skipped or budget.truncated,
        method=method,
    )
