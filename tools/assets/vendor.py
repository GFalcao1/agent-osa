"""Vendor the panel's third-party assets and keep ``static/MANIFEST.json`` honest.

Three sub-commands, all run from the repository root::

    uv run python tools/assets/vendor.py fetch      # npm pack the pinned packages, copy files
    uv run python tools/assets/vendor.py manifest   # recompute hashes of what is on disk
    uv run python tools/assets/vendor.py check      # fail if a hash or a pin does not match

``fetch`` needs Node/npm and the network. It downloads each pinned tarball into a scratch
directory, refuses it unless its sha512 equals the pinned registry integrity value, and
copies only the files the panel uses: the Phosphor SVGs listed in ``ICONS``, the font files
listed in ``FONTS`` (IBM Plex Sans, Serif and Mono, taken unmodified from the ``@fontsource``
packages, with the OFL text of each) and the light
SVG-renderer build of lottie-web (no expression engine, so no ``eval`` / ``new Function``).
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
        "package": "@fontsource-variable/ibm-plex-sans",
        "version": "5.3.0",
        "integrity": (
            "sha512-agG8tXFEo0hD9+J7npa4vbbWult52eMLVaQ6WQRlhs/iCAojrMAoejru85W9HTVXHfyUj96KM7gp"
            "/KGAS87XaQ=="
        ),
        "license": "OFL-1.1",
        "url": "https://github.com/IBM/plex",
        "font_version": "3.201",
    },
    "plex-serif": {
        "package": "@fontsource/ibm-plex-serif",
        "version": "5.3.0",
        "integrity": (
            "sha512-zwPfHB7EJbS5B8qnitPigJYzNdnt6PUeaoOA1gAAt//1pjpTVZN5sOU14NIAZ+C1xypL6GWsUG2VMYJW"
            "9bhJqQ=="
        ),
        "license": "OFL-1.1",
        "url": "https://github.com/IBM/plex",
        "font_version": "2.6",
    },
    "plex-mono": {
        "package": "@fontsource/ibm-plex-mono",
        "version": "5.3.0",
        "integrity": (
            "sha512-eTgnZjZEGk1QtD3ZstF+Vclo2HLAni8YMy34/DxllwZvyz1lR/1RF/xTiAquOBO7MvqBx8D2Ig2WCPMV"
            "fdZu7Q=="
        ),
        "license": "OFL-1.1",
        "url": "https://github.com/IBM/plex",
        "font_version": "2.3",
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
# as the @fontsource packages publish them (IBM Plex Sans variable for text and controls, IBM
# Plex Serif 600 for titles and counts, IBM Plex Mono 400 for folders and codes). None of the
# three declares a Reserved Font Name (licence header and the fonts' own copyright string), and
# nothing is subset, converted or renamed here, so the OFL conditions reduce to shipping the
# licence text.
FONT_PACKAGES = ("plex-sans", "plex-serif", "plex-mono")
FONTS: dict[str, tuple[str, str]] = {
    "ibm-plex-sans-latin-wght-normal.woff2": (
        "plex-sans",
        "files/ibm-plex-sans-latin-wght-normal.woff2",
    ),
    "ibm-plex-sans-latin-ext-wght-normal.woff2": (
        "plex-sans",
        "files/ibm-plex-sans-latin-ext-wght-normal.woff2",
    ),
    "ibm-plex-serif-latin-600-normal.woff2": (
        "plex-serif",
        "files/ibm-plex-serif-latin-600-normal.woff2",
    ),
    "ibm-plex-serif-latin-ext-600-normal.woff2": (
        "plex-serif",
        "files/ibm-plex-serif-latin-ext-600-normal.woff2",
    ),
    "ibm-plex-mono-latin-400-normal.woff2": (
        "plex-mono",
        "files/ibm-plex-mono-latin-400-normal.woff2",
    ),
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
            shutil.copyfile(packs[key] / "LICENSE", fonts / target)
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
