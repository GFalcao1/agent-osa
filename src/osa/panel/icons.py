"""Phosphor icons for the templates: ``{{ icon("house") }}``, ``{{ icon("lock", "duotone") }}``.

The SVG files live in ``static/icons`` (vendored from ``@phosphor-icons/core``, MIT; see
``THIRD_PARTY_NOTICES.md``) and are inlined, so the page needs no image request and the
icon takes the text colour (``currentColor``). Inlined markup is trusted output, so it is
emitted only under three conditions, all checked when the panel starts (``verify``, called by
``assets.register``; a failure stops the panel before it serves anything):

* the file is listed in ``static/MANIFEST.json`` (the fixed allowlist; a template can ask for
  a name, never for a path, and an unknown name raises ``UnknownIcon``);
* its bytes still hash to the manifest's sha256 (a tampered file is refused);
* its text is exactly one ``<svg>`` holding only ``<path d="..."/>`` elements (the only
  attribute besides ``d`` is the duotone ``opacity``): no script, style, link or handler can
  ride along.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from functools import cache
from typing import Final

from markupsafe import Markup

from osa.panel.assets import STATIC_DIR, AssetIntegrityError

WEIGHTS: Final = ("regular", "fill", "duotone")

_NAME: Final = re.compile(
    r"^(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*?)(?:-(?P<weight>fill|duotone))?\.svg$"
)
_SVG: Final = re.compile(
    r'^<svg xmlns="http://www\.w3\.org/2000/svg" viewBox="0 0 256 256" fill="currentColor">'
    r'(?P<body>(?:<path d="[0-9A-Za-z.,\- ]+"(?: opacity="0\.2")?/>)+)</svg>\n?$'
)
_CLASS: Final = re.compile(r"^[a-z][a-z0-9_-]*(?: [a-z][a-z0-9_-]*)*$")


class UnknownIcon(ValueError):
    """The template asked for an icon that is not in the vetted set."""


@cache
def _vetted() -> dict[tuple[str, str], str]:
    manifest = json.loads((STATIC_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    found: dict[tuple[str, str], str] = {}
    for relative, meta in manifest["files"].items():
        folder, _, filename = relative.partition("/")
        if folder != "icons":
            continue
        parsed = _NAME.match(filename)
        if parsed is None:
            raise AssetIntegrityError(f"unexpected icon file name: {relative}")
        raw = read_shipped_icon(relative)
        if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
            raise AssetIntegrityError(f"{relative} does not match the manifest")
        shape = _SVG.match(raw.decode("utf-8"))
        if shape is None:
            raise AssetIntegrityError(f"{relative} is not a plain path-only SVG")
        found[(parsed["name"], parsed["weight"] or "regular")] = shape["body"]
    return found


def read_shipped_icon(relative: str) -> bytes:
    """Bytes of one icon file; a missing file is an integrity failure."""
    try:
        return (STATIC_DIR / relative).read_bytes()
    except FileNotFoundError:
        raise AssetIntegrityError(f"{relative} is missing") from None


def verify() -> None:
    """Re-read and check every icon now (startup); raises ``AssetIntegrityError``."""
    _vetted.cache_clear()
    _vetted()


def available() -> frozenset[tuple[str, str]]:
    """Every ``(name, weight)`` pair that ``icon`` accepts."""
    return frozenset(_vetted())


def icon(name: str, weight: str = "regular", css_class: str = "") -> Markup:
    """Inline SVG of one vetted Phosphor icon; decorative (``aria-hidden``), sized by CSS."""
    body = _vetted().get((name, weight))
    if body is None:
        raise UnknownIcon(f"unknown icon {name!r} in weight {weight!r}")
    if css_class and not _CLASS.match(css_class):
        raise UnknownIcon(f"invalid icon class {css_class!r}")
    classes = html.escape(f"icon {css_class}".strip(), quote=True)
    return Markup(  # noqa: S704  # body is a manifest-hashed, regex-checked constant
        f'<svg class="{classes}" viewBox="0 0 256 256" fill="currentColor" '
        f'aria-hidden="true" focusable="false">{body}</svg>'
    )
