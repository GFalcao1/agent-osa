"""Deterministic generator of the panel's Lottie animations.

Run from the repository root::

    uv run python tools/lottie/generate.py

It writes three small JSON files into ``src/osa/panel/static/lottie/``. Same input, same
bytes: no clock, no randomness, floats rounded. Every animation is authored here from
basic shapes (ellipses, rectangles, polylines, stars), so nothing is third-party content.

Conventions the panel CSS and ``motion.js`` rely on:

* canvas 64 x 64, 30 fps, no expressions, no images, no text, no effects;
* layers carry a class (``cl``) that the stylesheet maps to the theme tokens
  (``lt-stroke`` -> currentColor, ``lt-gold-fill`` -> ``--gold``, ...), so the colours
  written below are only a fallback that CSS overrides in light and dark;
* every animation ends on a frame that is a good static picture (used when the visitor
  asks for reduced motion: the script jumps to the last frame and stops).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parents[2] / "src" / "osa" / "panel" / "static" / "lottie"
FPS = 30
SIZE = 64
K = 0.5522847498  # circle bezier constant

INK = [0.12, 0.23, 0.58, 1]  # fallback only (CSS paints currentColor)
GOLD = [0.72, 0.56, 0.23, 1]


def r2(value: float) -> float:
    return round(value, 2)


def static(value: Any) -> dict[str, Any]:
    if isinstance(value, (list, tuple)):
        value = [r2(v) for v in value]
    elif isinstance(value, float):
        value = r2(value)
    return {"a": 0, "k": value}


def ease(x1: float = 0.3, y1: float = 0.0, x2: float = 0.2, y2: float = 1.0) -> dict[str, Any]:
    """Cubic-bezier easing: (x1, y1) is the out handle, (x2, y2) the in handle."""
    return {"o": {"x": x1, "y": y1}, "i": {"x": x2, "y": y2}}


LINEAR = ease(0.0, 0.0, 1.0, 1.0)
SOFT = ease(0.3, 0.0, 0.2, 1.0)
BOUNCE = ease(0.3, 0.0, 0.25, 1.35)


def animated(frames: list[tuple[int, Any]], curve: dict[str, Any] | None = None) -> dict[str, Any]:
    """Keyframes ``(time, value)``; each segment uses ``curve`` (or its own as a 3rd item)."""
    keys: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):
        time, value = frame[0], frame[1]
        seg = frame[2] if len(frame) > 2 else (curve or SOFT)
        vec = list(value) if isinstance(value, (list, tuple)) else [value]
        vec = [r2(v) for v in vec]
        key: dict[str, Any] = {"t": time, "s": vec}
        if index < len(frames) - 1:
            nxt = frames[index + 1][1]
            end = list(nxt) if isinstance(nxt, (list, tuple)) else [nxt]
            key["e"] = [r2(v) for v in end]
            key.update(seg)
        keys.append(key)
    return {"a": 1, "k": keys}


def transform(**kw: Any) -> dict[str, Any]:
    """Group transform item (``tr``) with sensible defaults."""
    return {
        "ty": "tr",
        "p": kw.get("p", static([0, 0])),
        "a": kw.get("a", static([0, 0])),
        "s": kw.get("s", static([100, 100])),
        "r": kw.get("r", static(0)),
        "o": kw.get("o", static(100)),
        "sk": static(0),
        "sa": static(0),
    }


def group(name: str, items: list[dict[str, Any]], **tr: Any) -> dict[str, Any]:
    return {"ty": "gr", "nm": name, "it": [*items, transform(**tr)], "bm": 0}


def stroke(width: float, color: list[float] = INK, opacity: Any = None) -> dict[str, Any]:
    return {
        "ty": "st",
        "c": static(color),
        "o": opacity or static(100),
        "w": static(width),
        "lc": 2,
        "lj": 2,
        "bm": 0,
    }


def fill(color: list[float] = GOLD, opacity: Any = None) -> dict[str, Any]:
    return {"ty": "fl", "c": static(color), "o": opacity or static(100), "r": 1, "bm": 0}


def trim(end: Any, start: Any = None) -> dict[str, Any]:
    return {"ty": "tm", "s": start or static(0), "e": end, "o": static(0), "m": 1}


def ellipse(radius: float, center: tuple[float, float] = (0, 0)) -> dict[str, Any]:
    return {"ty": "el", "p": static(list(center)), "s": static([radius * 2, radius * 2]), "d": 1}


def star(outer: float, inner: float, points: int = 4) -> dict[str, Any]:
    return {
        "ty": "sr",
        "sy": 1,
        "d": 1,
        "pt": static(points),
        "p": static([0, 0]),
        "r": static(0),
        "ir": static(inner),
        "is": static(0),
        "or": static(outer),
        "os": static(0),
    }


def polyline(points: list[tuple[float, float]], closed: bool = False) -> dict[str, Any]:
    zero = [[0, 0]] * len(points)
    return {
        "ty": "sh",
        "ks": static({"i": zero, "o": zero, "v": [[r2(x), r2(y)] for x, y in points], "c": closed}),
    }


def shape_path(vertices: dict[str, Any] | Any) -> dict[str, Any]:
    return {"ty": "sh", "ks": vertices}


def layer(
    index: int,
    name: str,
    css_class: str,
    shapes: list[dict[str, Any]],
    end: int,
    *,
    position: Any = None,
    rotation: Any = None,
    scale: Any = None,
    opacity: Any = None,
    anchor: Any = None,
) -> dict[str, Any]:
    return {
        "ddd": 0,
        "ind": index,
        "ty": 4,
        "nm": name,
        "cl": css_class,
        "sr": 1,
        "ks": {
            "o": opacity or static(100),
            "r": rotation or static(0),
            "p": position or static([SIZE / 2, SIZE / 2, 0]),
            "a": anchor or static([0, 0, 0]),
            "s": scale or static([100, 100, 100]),
        },
        "ao": 0,
        "shapes": shapes,
        "ip": 0,
        "op": end,
        "st": 0,
        "bm": 0,
    }


def document(name: str, end: int, layers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "v": "5.7.0",
        "fr": FPS,
        "ip": 0,
        "op": end,
        "w": SIZE,
        "h": SIZE,
        "nm": name,
        "ddd": 0,
        "assets": [],
        "layers": layers,
    }


def pos(x: float, y: float) -> list[float]:
    return [x, y, 0]


def scl(v: float) -> list[float]:
    return [v, v, 100]


# --- animations ------------------------------------------------------------------------


def check() -> dict[str, Any]:
    """Success: ring draws, check draws, a few gold sparkles pop once (50 frames)."""
    end = 50
    ring = group(
        "ring",
        [ellipse(20.5), trim(animated([(0, 0), (22, 100)], SOFT)), stroke(3.8)],
    )
    tick = group(
        "check",
        [
            polyline([(-10, 1), (-2.5, 9), (11, -7)]),
            trim(animated([(12, 0), (30, 100)], SOFT)),
            stroke(4.2),
        ],
    )
    sparks = []
    for n, (angle, start) in enumerate(((-50, 20), (30, 24), (150, 28), (-135, 32))):
        rad = math.radians(angle)
        dist = 28
        px, py = math.cos(rad) * dist, math.sin(rad) * dist
        sparks.append(
            group(
                f"spark{n}",
                [star(6.5, 1.7), fill(GOLD)],
                p=static([r2(px), r2(py)]),
                s=animated(
                    [(start, [0, 0]), (start + 8, [100, 100]), (start + 18, [0, 0])],
                    BOUNCE,
                ),
                r=animated([(start, 0), (start + 18, 45)], LINEAR),
            )
        )
    return document(
        "check",
        end,
        [
            layer(1, "sparkles", "lt-gold-fill", sparks, end),
            layer(2, "tick", "lt-stroke", [tick], end),
            layer(3, "ring", "lt-stroke", [ring], end),
        ],
    )


def spinner() -> dict[str, Any]:
    """Loop: a short arc turns around a faint track (30 frames per turn)."""
    end = 30
    track = group("track", [ellipse(25), stroke(7, opacity=static(22))])
    arc = group("arc", [ellipse(25), trim(static(30)), stroke(7)])
    return document(
        "spinner",
        end,
        [
            layer(
                1,
                "arc",
                "lt-stroke",
                [arc],
                end,
                rotation=animated([(0, -90), (30, 270)], LINEAR),
            ),
            layer(2, "track", "lt-stroke", [track], end),
        ],
    )


def _arc(
    cx: float, cy: float, radius: float, a0: float, a1: float
) -> tuple[list[float], list[float], list[float], list[float]]:
    """Cubic from angle ``a0`` to ``a1`` (degrees, y down): points and relative handles."""
    t0, t1 = math.radians(a0), math.radians(a1)
    sign = 1 if a1 > a0 else -1
    handle = 4 / 3 * math.tan(abs(t1 - t0) / 4) * radius
    p0 = [cx + radius * math.cos(t0), cy + radius * math.sin(t0)]
    p1 = [cx + radius * math.cos(t1), cy + radius * math.sin(t1)]
    out0 = [-math.sin(t0) * handle * sign, math.cos(t0) * handle * sign]
    in1 = [math.sin(t1) * handle * sign, -math.cos(t1) * handle * sign]
    return p0, out0, in1, p1


def _closed(
    segments: list[tuple[list[float], list[float], list[float], list[float]]],
) -> dict[str, Any]:
    """Closed bezier from consecutive arcs (each starts where the previous one ends)."""
    verts = [s[0] for s in segments]
    outs = [s[1] for s in segments]
    ins = [segments[i - 1][2] for i in range(len(segments))]
    return {
        "i": [[r2(x), r2(y)] for x, y in ins],
        "o": [[r2(x), r2(y)] for x, y in outs],
        "v": [[r2(x), r2(y)] for x, y in verts],
        "c": True,
    }


def _sun_disc(radius: float) -> dict[str, Any]:
    return _closed([_arc(0, 0, radius, a, a + 90) for a in (270, 360, 450, 540)])


def _moon(radius: float) -> dict[str, Any]:
    """Crescent with four vertices (tip, inner middle, tip, outer left) to morph from the disc."""
    tip_x = radius * 4 / 11
    tip_y = math.sqrt(radius**2 - tip_x**2)
    inner_mid = -radius * 3 / 11
    inner_cx = (tip_x**2 + tip_y**2 - inner_mid**2) / (2 * (tip_x - inner_mid))
    inner_r = inner_cx - inner_mid
    a_tip = math.degrees(math.atan2(-tip_y, tip_x - inner_cx))  # top tip seen from inner centre
    b_tip = math.degrees(math.atan2(tip_y, tip_x - inner_cx))
    top = math.degrees(math.atan2(-tip_y, tip_x))
    bottom = math.degrees(math.atan2(tip_y, tip_x))
    # a_tip is near -111 deg (= 249): go to 180 (inner middle) then on to b_tip (111).
    seg_am = _arc(inner_cx, 0, inner_r, a_tip + 360, 180)
    seg_mb = _arc(inner_cx, 0, inner_r, 180, b_tip)
    seg_bl = _arc(0, 0, radius, bottom, 180)
    seg_la = _arc(0, 0, radius, 180, top + 360)
    return _closed([seg_am, seg_mb, seg_bl, seg_la])


def theme() -> dict[str, Any]:
    """Sun and moon: frames 0-24 sun -> moon, 24-48 moon -> sun (frame 0 and 24 are the rests)."""
    end = 49
    sun, moon = _sun_disc(10.5), _moon(11.5)
    body = group("body", [shape_path(_morph([(0, sun), (24, moon), (48, sun)])), fill(INK)])
    rays = []
    for n in range(8):
        a = math.radians(n * 45)
        rays.append(
            polyline([(math.cos(a) * 16, math.sin(a) * 16), (math.cos(a) * 22, math.sin(a) * 22)])
        )
    ray_group = group(
        "rays",
        [
            *rays,
            trim(animated([(0, 100), (10, 0), (36, 0), (48, 100)], SOFT)),
            stroke(4),
        ],
    )
    star_group = group(
        "star",
        [star(5, 1.3), fill(GOLD)],
        p=static([14, -14]),
        s=animated(
            [(0, [0, 0]), (12, [0, 0]), (22, [100, 100]), (30, [100, 100]), (38, [0, 0])], BOUNCE
        ),
    )
    return document(
        "theme",
        end,
        [
            layer(1, "star", "lt-gold-fill", [star_group], end),
            layer(
                2,
                "body",
                "lt-fill",
                [body],
                end,
                rotation=animated([(0, 0), (24, -28), (48, 0)], BOUNCE),
            ),
            layer(
                3,
                "rays",
                "lt-stroke",
                [ray_group],
                end,
                rotation=animated([(0, 0), (24, 90), (48, 180)], SOFT),
            ),
        ],
    )


def _morph(frames: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    """Animated bezier path through ``frames`` (same vertex count in each)."""
    keys: list[dict[str, Any]] = []
    for index, (time, shape) in enumerate(frames):
        key: dict[str, Any] = {"t": time, "s": [shape]}
        if index < len(frames) - 1:
            key["e"] = [frames[index + 1][1]]
            key.update(SOFT)
        keys.append(key)
    return {"a": 1, "k": keys}


ANIMATIONS = {
    "check": check,
    "spinner": spinner,
    "theme": theme,
}


def render(document_: dict[str, Any]) -> str:
    return json.dumps(document_, separators=(",", ":"), sort_keys=False) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in ANIMATIONS.items():
        text = render(build())
        (OUT / f"{name}.json").write_text(text, encoding="utf-8", newline="\n")
        print(f"{name}.json  {len(text.encode())} bytes")


if __name__ == "__main__":
    main()
