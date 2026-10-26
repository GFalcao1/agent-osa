"""Vendor the panel's third-party assets and keep ``static/MANIFEST.json`` honest.

Three sub-commands, all run from the repository root::

    uv run python tools/assets/vendor.py fetch      # npm pack the pinned packages, copy files
    uv run python tools/assets/vendor.py manifest   # recompute hashes of what is on disk
    uv run python tools/assets/vendor.py check      # fail if a hash or a pin does not match

``fetch`` needs Node/npm and the network. It downloads each pinned tarball into a scratch
directory, refuses it unless its sha512 equals the pinned registry integrity value, and
copies only the files the panel uses: the Phosphor SVGs listed in ``ICONS``, the font files
listed in ``FONTS`` (IBM Plex Sans, Serif and Mono, copied byte for byte from IBM's own
``@ibm/plex-*`` packages, with the licence text of each) and the light SVG-renderer build of
lottie-web (no expression engine, so no ``eval`` / ``new Function``).
That build still carries an ``XMLHttpRequest`` loader (used only for ``path:``) and a Blob-URL
``Worker`` (used only after ``lottie.useWebWorker(true)``); neither is reachable, because
``motion.js`` passes ``animationData`` only and never calls ``useWebWorker`` (pinned by a
test), and ``script-src 'self'`` would refuse a ``blob:`` worker anyway. Nothing else is
copied. ``favicon.svg`` is the panel's own hand-drawn mark (hashed, not fetched).
``motion.js`` is the panel's own source (reviewed in the repository like any code), so it is
not hashed here.

To update a package: change its pin below (version and the integrity that
``npm view <package>@<version> dist.integrity`` prints), run ``fetch``, then ``manifest``,
review the diff and run the test suite.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "osa" / "panel" / "static"
MANIFEST = STATIC / "MANIFEST.json"

PINS: dict[str, dict[str, str]] = {
    "phosphor": {
        "package": "@phosphor-icons/core",
        "version": "2.1.1",
        "integrity": (
            "sha512-v4ARvrip4qBCImOE5rmPUylOEK4iiED9ZyKjcvzuezqMaiRASCHKcRIuvvxL/twvLpkfnEODCOJp5"
            "dM4eZilxQ=="
        ),
        "license": "MIT",
        "url": "https://github.com/phosphor-icons/core",
    },
    "plex-sans": {
        "package": "@ibm/plex-sans",
        "version": "1.1.0",
        "integrity": (
            "sha512-WPgvO6Yfj2w5YbhyAr1tv95RUz4LRJlqN+CmYvBglabXteufP1D1E9BABMde+ZIKdRbFJDoKF5eQzfhp"
            "nbgZcQ=="
        ),
        "license": "OFL-1.1",
        "url": "https://github.com/IBM/plex",
        "font_version": "3.005",
    },
    "plex-serif": {
        "package": "@ibm/plex-serif",
        "version": "2.0.0",
        "integrity": (
            "sha512-xVu9JsC18tBoQF2M6fofpsWrHj5a94saxlOZbYmXoC0E3UfjgS1JlibgrnaUJjnkLlEyCpMgI2Pdhk"
            "Dxijw0gA=="
        ),
        "license": "OFL-1.1",
        "url": "https://github.com/IBM/plex",
        "font_version": "3.006",
    },
    "plex-mono": {
        "package": "@ibm/plex-mono",
        "version": "2.5.0",
        "integrity": (
            "sha512-STBJIPxPomOYPmBMO7z5TKPJUotAF9u3gAUumTqVgwgrAO+K4FRNh0MlhsoJjKhJKsMbBJR10/bk4i"
            "nkj/wc1w=="
        ),
        "license": "OFL-1.1",
        "url": "https://github.com/IBM/plex",
        "font_version": "2.005",
    },
    "lottie": {
        "package": "lottie-web",
        "version": "5.13.0",
        "integrity": (
            "sha512-+gfBXl6sxXMPe8tKQm7qzLnUy5DUPJPKIyRHwtpCpyUEYjHYRJC/5gjUvdkuO2c3JllrPtHXH5UJJK8"
            "LRYl5yQ=="
        ),
        "license": "MIT",
        "url": "https://github.com/airbnb/lottie-web",
        "file": "build/player/lottie_light.min.js",
    },
}

# name -> weights actually used by the templates (regular = controls, fill = identity and the
# active section, duotone = informative and illustrative). Phosphor names its files
# ``<name>.svg`` for regular and ``<name>-<weight>.svg`` for the others.
ICONS: dict[str, tuple[str, ...]] = {
    "house": ("regular", "fill"),
    "hard-drives": ("regular", "fill"),
    "list-numbers": ("regular", "fill"),
    "buildings": ("regular", "fill"),
    "users": ("regular", "fill"),
    "cpu": ("regular", "fill"),
    "book-open": ("regular", "fill"),
    "sun": ("regular",),
    "moon": ("regular",),
    "plus": ("regular",),
    "floppy-disk": ("regular",),
    "arrow-up": ("regular",),
    "arrow-down": ("regular",),
    "arrow-left": ("regular",),
    "arrow-right": ("regular",),
    "power": ("regular",),
    "trash": ("regular",),
    "lock": ("duotone",),
    "warning": ("duotone",),
    "check-circle": ("duotone",),
    "info": ("duotone",),
    "x-circle": ("duotone",),
}

LOTTIE_FILES = ("check", "spinner", "theme")

# Fonts: file under static/fonts/ -> (pin key, file inside the package). Shipped byte for byte
# as IBM, the copyright holder, publishes them in its own npm packages (``fonts/split/woff2``:
# IBM's own web builds, cut by IBM into character-range subsets). Files produced and published
# by the copyright holder are not third-party "Modified Versions" under the OFL, so they keep
# their internal names; only the local file names are lower-cased here. Used: IBM Plex Sans
# 400/500/600 for text and controls (IBM publishes no variable web build), IBM Plex Serif 600
# for titles and counts, IBM Plex Mono 400 for folders and codes. "Latin1" is what every page
# needs; "Latin2" (Latin extended) is only fetched for a character in its ``unicode-range``. The
# panel CSS still exposes neutral family aliases ("Panel Sans", "Panel Serif", "Panel Mono"; a
# test refuses "Plex" in any font-family), and the licence text of each package (IBM's
# ``LICENSE.txt``, Reserved Font Name line included) ships next to the files.
_SPLIT = "fonts/split/woff2"
FONT_PACKAGES = ("plex-sans", "plex-serif", "plex-mono")
FONTS: dict[str, tuple[str, str]] = {
    **{
        f"ibm-plex-sans-{weight}-{subset.lower()}.woff2": (
            "plex-sans",
            f"{_SPLIT}/IBMPlexSans-{style}-{subset}.woff2",
        )
        for weight, style in (("400", "Regular"), ("500", "Medium"), ("600", "SemiBold"))
        for subset in ("Latin1", "Latin2")
    },
    "ibm-plex-serif-600-latin1.woff2": (
        "plex-serif",
        f"{_SPLIT}/IBMPlexSerif-SemiBold-Latin1.woff2",
    ),
    "ibm-plex-serif-600-latin2.woff2": (
        "plex-serif",
        f"{_SPLIT}/IBMPlexSerif-SemiBold-Latin2.woff2",
    ),
    "ibm-plex-mono-400-latin1.woff2": ("plex-mono", f"{_SPLIT}/IBMPlexMono-Regular-Latin1.woff2"),
}
# Licence text shipped next to the fonts (the OFL asks every copy to carry it).
FONT_LICENSES: dict[str, str] = {
    "OFL-ibm-plex-sans.txt": "plex-sans",
    "OFL-ibm-plex-serif.txt": "plex-serif",
    "OFL-ibm-plex-mono.txt": "plex-mono",
}


def icon_file(name: str, weight: str) -> str:
    return f"{name}.svg" if weight == "regular" else f"{name}-{weight}.svg"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sri(path: Path) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()


def pack(spec: dict[str, str], scratch: Path) -> Path:
    """``npm pack`` one pinned package, verify its integrity, return the extracted dir."""
    target = scratch / spec["package"].replace("/", "_").replace("@", "")
    target.mkdir(parents=True)
    out = subprocess.run(  # noqa: S603  # fixed argv, pinned spec
        ["npm", "pack", f"{spec['package']}@{spec['version']}", "--silent"],
        cwd=target,
        capture_output=True,
        text=True,
        check=True,
    )
    tarball = target / out.stdout.strip().splitlines()[-1]
    if sri(tarball) != spec["integrity"]:
        raise SystemExit(f"integrity mismatch for {spec['package']}@{spec['version']}")
    with tarfile.open(tarball) as archive:
        archive.extractall(target / "x", filter="data")
    return target / "x" / "package"


def fetch() -> None:
    with tempfile.TemporaryDirectory(prefix="osa-assets-") as tmp:
        scratch = Path(tmp)
        phosphor = pack(PINS["phosphor"], scratch)
        lottie = pack(PINS["lottie"], scratch)
        packs = {key: pack(PINS[key], scratch) for key in FONT_PACKAGES}
        icons = STATIC / "icons"
        if icons.exists():
            shutil.rmtree(icons)
        icons.mkdir(parents=True)
        for name, weights in ICONS.items():
            for weight in weights:
                source = phosphor / "assets" / weight / icon_file(name, weight)
                shutil.copyfile(source, icons / icon_file(name, weight))
        vendor = STATIC / "vendor"
        vendor.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(lottie / PINS["lottie"]["file"], vendor / "lottie_light.min.js")
        fonts = STATIC / "fonts"
        if fonts.exists():
            shutil.rmtree(fonts)
        fonts.mkdir(parents=True)
        for target, (key, inside) in FONTS.items():
            shutil.copyfile(packs[key] / inside, fonts / target)
        for target, key in FONT_LICENSES.items():
            shutil.copyfile(packs[key] / "LICENSE.txt", fonts / target)
    print("fetched; now run: manifest")


def build_manifest() -> dict[str, Any]:
    files: dict[str, dict[str, str]] = {}

    def add(relative: str, origin: str) -> None:
        files[relative] = {"sha256": sha256(STATIC / relative), "origin": origin}

    for name, weights in ICONS.items():
        for weight in weights:
            add(f"icons/{icon_file(name, weight)}", "phosphor")
    add("vendor/lottie_light.min.js", "lottie")
    for name, (key, _inside) in FONTS.items():
        add(f"fonts/{name}", key)
    for name, key in FONT_LICENSES.items():
        add(f"fonts/{name}", f"{key}-licence")
    for name in LOTTIE_FILES:
        add(f"lottie/{name}.json", "generator")
    add("favicon.svg", "own")
    return {"schema": 1, "sources": PINS, "files": dict(sorted(files.items()))}


def manifest() -> None:
    MANIFEST.write_text(
        json.dumps(build_manifest(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {MANIFEST.relative_to(ROOT)}")


def check() -> int:
    on_disk = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = build_manifest()
    if on_disk != expected:
        print("MANIFEST.json does not match the files and pins; run: manifest", file=sys.stderr)
        return 1
    print("manifest ok")
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "fetch":
        fetch()
    elif command == "manifest":
        manifest()
    elif command == "check":
        return check()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
