"""Value objects of the extraction layer: limits and results (spec §7)."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from osa.core.settings import Settings

Method = Literal["pdf_text", "pdf_scanned", "pdf_mixed", "docx", "xlsx", "image"]
METHODS: tuple[str, ...] = ("pdf_text", "pdf_scanned", "pdf_mixed", "docx", "xlsx", "image")

DEFAULT_SENSITIVE_EXTENSIONS: tuple[str, ...] = (".pfx", ".p12", ".pem", ".key", ".cer", ".crt")

MIB = 1024 * 1024
DEFAULT_MAX_OCR_PAGES = 10


@dataclass(frozen=True, slots=True)
class PageImage:
    """One page (or the standalone image) as PNG for the vision model."""

    data: bytes
    page: int  # 1-based; a standalone image is page 1
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class ExtractedContent:
    text: str  # at most ``max_text_chars``; OCR text of image pages is included
    images: tuple[PageImage, ...]  # at most ``max_image_pages``
    pages_total: int  # PDF pages, XLSX sheets; 1 for DOCX (no reliable pagination) and images
    truncated: bool  # some content was left out (pages, rows, characters)
    method: Method


def _positive(name: str, value: int | float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        raise ValueError(f"{name} must be a positive number")


@dataclass(frozen=True, slots=True)
class ExtractLimits:
    """Validated limits; ``max_text_chars`` and ``max_image_pages`` mirror Settings."""

    max_text_chars: int = 12000
    max_image_pages: int = 5
    sensitive_extensions: tuple[str, ...] = DEFAULT_SENSITIVE_EXTENSIONS
    # PDF: a page with fewer alphanumeric characters than this is treated as an image page.
    min_chars_per_page: int = 20
    # PDF: a page covered by an image (letterhead, searchable scan) with at least this many
    # alphanumeric characters keeps its text layer and skips OCR; below it (a scanner
    # stamp) the page goes through OCR.
    min_chars_text_over_image: int = 200
    # Image pages (PDF) that also get local OCR, in page order.
    max_ocr_pages: int = DEFAULT_MAX_OCR_PAGES
    max_xlsx_rows: int = 2000
    max_xlsx_cols: int = 64
    max_xlsx_sheets: int = 20
    max_file_bytes: int = 100 * MIB
    # OOXML (zip): entries, total declared size and bytes actually read per part.
    max_zip_entries: int = 2000
    max_unzipped_bytes: int = 200 * MIB
    max_part_bytes: int = 64 * MIB
    # Images: source pixels (checked from the header, before decoding) and the OCR input
    # size, which is also the engine's own limit (RapidOCR's default is 2000).
    max_pixels: int = 100_000_000
    max_ocr_side: int = 2000
    # Image sent to the vision model: longest side in pixels.
    max_image_side: int = 1600
    # PDF raster: dpi for OCR and the cap on pixels per rendered page (checked before render).
    render_dpi: int = 200
    max_render_pixels: int = 25_000_000
    # Isolation: wall-clock seconds and resident memory of the worker process.
    timeout_s: float = 120.0
    max_memory_mb: int = 4096

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "sensitive_extensions":
                if not isinstance(value, tuple) or not value:
                    raise ValueError("sensitive_extensions must be a non-empty tuple")
                if not all(isinstance(e, str) and e.startswith(".") and len(e) > 1 for e in value):
                    raise ValueError("sensitive_extensions items must start with a dot")
            else:
                _positive(f.name, value)
        if self.min_chars_text_over_image < self.min_chars_per_page:
            raise ValueError("min_chars_text_over_image must be at least min_chars_per_page")
        if self.max_ocr_pages < self.max_image_pages:
            raise ValueError("max_ocr_pages must be at least max_image_pages")
        if self.max_part_bytes > self.max_unzipped_bytes:
            raise ValueError("max_part_bytes must not exceed max_unzipped_bytes")

    @classmethod
    def from_settings(cls, settings: Settings) -> ExtractLimits:
        return cls(
            max_text_chars=settings.max_text_chars,
            max_image_pages=settings.max_image_pages,
            sensitive_extensions=tuple(settings.sensitive_extensions),
            max_ocr_pages=max(settings.max_image_pages, DEFAULT_MAX_OCR_PAGES),
        )


class TextBudget:
    """Accumulates text up to a character cap and remembers whether anything was cut."""

    def __init__(self, cap: int) -> None:
        self._cap = cap
        self._parts: list[str] = []
        self._size = 0
        self.truncated = False

    @property
    def full(self) -> bool:
        return self._size >= self._cap

    @property
    def size(self) -> int:
        return self._size

    def add(self, piece: str, sep: str = "\n") -> bool:
        """Append ``piece``; return False once the cap is reached (the piece is cut)."""
        if not piece:
            return not self.full
        if self.full:
            self.truncated = True
            return False
        if self._parts:
            piece = sep + piece
        room = self._cap - self._size
        if len(piece) > room:
            piece = piece[:room]
            self.truncated = True
        self._parts.append(piece)
        self._size += len(piece)
        return not self.full

    def text(self) -> str:
        return "".join(self._parts)
