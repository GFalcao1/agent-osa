"""Static files of the panel: the motion script, the Lottie library, animations, fonts, favicon.

Everything is self-hosted and served by the panel itself, behind the same peer/Host filter and
security headers as ``/panel.css`` (the middleware wraps every route; nothing here bypasses
it). The routes are an exact allowlist: each public path below is its own route with no path
parameter, so there is no directory listing, no traversal, and no query-driven file choice.
The bytes are read once, from the package directory (not the working directory), when the
application is built; a request never touches the filesystem.

Fonts (``/fonts/<name>.<hash>.woff2``) are the one kind of response the panel lets a browser
keep: the file name carries the first ten hex digits of the file's sha256 (read from the
manifest), so a changed font is a new URL and an old URL can never serve other bytes. They are
the only ``immutable`` assets; ``osa.panel.app`` gives exactly those paths a long ``private``
cache lifetime and everything else stays ``no-store``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fastapi import FastAPI
from starlette.responses import Response

STATIC_DIR: Final = Path(__file__).resolve().parent / "static"
JAVASCRIPT: Final = "text/javascript"
JSON: Final = "application/json"
SVG: Final = "image/svg+xml"
WOFF2: Final = "font/woff2"

LOTTIE_NAMES: Final = ("check", "spinner", "theme")
# Logical font name -> file under static/fonts/ (the exact set; nothing else is served).
# The logical names (family, weight, IBM subset) are what the stylesheet and templates use, so
# changing a typeface only touches this table, the vendor pins and the face rules in
# ``styles.py``. "latin1" is IBM's Latin subset (every page); "latin2" is IBM's Latin-extended
# subset, fetched only for a character in its ``unicode-range``.
FONT_FILES: Final = {
    "sans-400-latin1": "ibm-plex-sans-400-latin1.woff2",
    "sans-400-latin2": "ibm-plex-sans-400-latin2.woff2",
    "sans-500-latin1": "ibm-plex-sans-500-latin1.woff2",
    "sans-500-latin2": "ibm-plex-sans-500-latin2.woff2",
    "sans-600-latin1": "ibm-plex-sans-600-latin1.woff2",
    "sans-600-latin2": "ibm-plex-sans-600-latin2.woff2",
    "serif-600-latin1": "ibm-plex-serif-600-latin1.woff2",
    "serif-600-latin2": "ibm-plex-serif-600-latin2.woff2",
    "mono-400-latin1": "ibm-plex-mono-400-latin1.woff2",
}
_HASH_LENGTH: Final = 10


@dataclass(frozen=True)
class Asset:
    path: str  # public URL path, exact match
    file: str  # location under static/ and key in MANIFEST.json
    content_type: str
    own: bool = False  # the panel's own source: reviewed in the repository, not in the manifest
    immutable: bool = False  # the URL carries the content hash: safe to cache for a year


class AssetIntegrityError(RuntimeError):
    """A shipped file does not match ``MANIFEST.json``."""


def _font_paths() -> dict[str, str]:
    """Logical name -> public path with the content hash, from the manifest."""
    data = json.loads((STATIC_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    files: dict[str, dict[str, str]] = data["files"]
    paths: dict[str, str] = {}
    for key, name in FONT_FILES.items():
        meta = files.get(f"fonts/{name}")
        if meta is None:
            raise AssetIntegrityError(f"fonts/{name} is not in the manifest")
        stem = name.removesuffix(".woff2")
        paths[key] = f"/fonts/{stem}.{meta['sha256'][:_HASH_LENGTH]}.woff2"
    return paths


FONT_PATHS: Final = _font_paths()


def font_path(key: str) -> str:
    """Public, hashed URL path of one font (stylesheet and ``<link rel="preload">``)."""
    return FONT_PATHS[key]


ASSETS: Final = (
    Asset("/motion.js", "motion.js", JAVASCRIPT, own=True),
    Asset("/favicon.svg", "favicon.svg", SVG),
    Asset("/assets/lottie.min.js", "vendor/lottie_light.min.js", JAVASCRIPT),
    *(Asset(f"/assets/lottie/{name}.json", f"lottie/{name}.json", JSON) for name in LOTTIE_NAMES),
    *(
        Asset(FONT_PATHS[key], f"fonts/{name}", WOFF2, immutable=True)
        for key, name in FONT_FILES.items()
    ),
)
IMMUTABLE_PATHS: Final = frozenset(asset.path for asset in ASSETS if asset.immutable)


def load() -> dict[str, tuple[bytes, str]]:
    """Read every allowlisted file once and check it against the manifest."""
    manifest = json.loads((STATIC_DIR / "MANIFEST.json").read_text(encoding="utf-8"))["files"]
    loaded: dict[str, tuple[bytes, str]] = {}
    for asset in ASSETS:
        data = read_shipped(asset.file)
        if asset.own:
            loaded[asset.path] = (data, asset.content_type)
            continue
        meta = manifest.get(asset.file)
        if meta is None:
            raise AssetIntegrityError(f"{asset.file} is not in the manifest")
        if hashlib.sha256(data).hexdigest() != meta["sha256"]:
            raise AssetIntegrityError(f"{asset.file} does not match the manifest")
        loaded[asset.path] = (data, asset.content_type)
    return loaded


def read_shipped(relative: str) -> bytes:
    """Bytes of one file under ``static/``; a missing file is an integrity failure."""
    try:
        return (STATIC_DIR / relative).read_bytes()
    except FileNotFoundError:
        raise AssetIntegrityError(f"{relative} is missing") from None


def register(application: FastAPI) -> None:
    """Verify every shipped file, then add one exact-path GET route per allowlisted asset.

    The icons are verified here too (hash and structure, read fresh from disk), so a tampered
    or missing file stops ``osa panel`` at startup with ``AssetIntegrityError`` instead of
    turning every page into a 500 at its first render.
    """
    from osa.panel import icons  # icons imports this module; imported here to avoid a cycle

    loaded = load()
    icons.verify()
    for path, (data, content_type) in loaded.items():
        application.add_api_route(path, _serve(data, content_type), include_in_schema=False)


def _serve(data: bytes, content_type: str) -> Callable[[], Response]:
    def endpoint() -> Response:
        return Response(data, media_type=content_type)

    return endpoint
