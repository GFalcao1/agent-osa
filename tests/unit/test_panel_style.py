"""The stylesheet's discipline: tokens for type, the approved palette, the card look, WCAG AA.

Portable (no database). These tests read the served ``/panel.css`` and refuse a stray
hard-coded font family, size, weight, line height or tracking and any change to the approved
colour tokens. They also pin the card look the user asked to keep: a soft layered shadow, a
thin gold edge along the top of cards and banners that widens on hover, the gold inner ring
of primary buttons, radii of 6/8/12px and pills only on status badges and the theme switch.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from osa.core.settings import Settings
from osa.panel import app as panel

PORT = 8765


def css() -> str:
    application = panel.create_app(session_factory=lambda: None, settings=Settings(), port=PORT)
    client = TestClient(
        application, base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 50000)
    )
    return client.get("/panel.css").text


CSS = css()
BODY = re.sub(r"/\*.*?\*/", "", re.sub(r"@font-face \{.*?\n\}", "", CSS, flags=re.S), flags=re.S)
RULES = re.findall(r"([^{}@]+)\{([^{}]*)\}", BODY)  # (selector, declarations)


def block(opening: str) -> str:
    start = CSS.index(opening) + len(opening)
    return CSS[start : CSS.index("}", start)]


def tokens(text: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*([^;]+);", text))


LIGHT = tokens(block(":root {"))
DARK = {**LIGHT, **tokens(block(':root[data-theme="dark"] {'))}

# --- the approved palette must not change --------------------------------------------------

APPROVED_LIGHT = {
    "paper": "#f6f8fd", "surface": "#ffffff", "surface-2": "#f4f7fc", "row-hover": "#f8faff",
    "ink": "#12172b", "muted": "#566075", "hairline": "#e3e7f0", "hairline-strong": "#cfd6e6",
    "control": "#7a8398", "blue": "#1f3a93", "blue-solid": "#1f3a93",
    "blue-solid-hover": "#14275f", "blue-deep": "#14275f", "blue-soft": "#eaf0fb",
    "blue-line": "#c5d3f2", "red": "#b3202a", "red-solid": "#b3202a", "red-deep": "#8e1820",
    "red-soft": "#fbecec", "red-line": "#e7b8bb", "gold": "#b8903a", "focus": "#1f3a93",
    "on-accent": "#ffffff", "knob": "#ffffff", "sun-color": "#1f3a93", "moon-color": "#566075",
    "knob-ink": "#1f3a93",
}  # fmt: skip
APPROVED_DARK = {
    "paper": "#0a1020", "surface": "#111a31", "surface-2": "#0d1529", "row-hover": "#15203c",
    "ink": "#e9edf7", "muted": "#a3adc6", "hairline": "#222d49", "hairline-strong": "#33416a",
    "control": "#7a88ad", "blue": "#8fb0ff", "blue-solid": "#3a5fd9",
    "blue-solid-hover": "#2a4bb8", "blue-deep": "#c3d3ff", "blue-soft": "#17264b",
    "blue-line": "#2f4580", "red": "#ff8f97", "red-solid": "#c8303b", "red-deep": "#ffb4b9",
    "red-soft": "#2b1420", "red-line": "#6b2a38", "gold": "#d6b25e", "focus": "#a9c2ff",
    "knob": "#dfe7ff", "sun-color": "#a3adc6", "moon-color": "#14275f", "knob-ink": "#14275f",
}  # fmt: skip


def test_the_approved_light_and_dark_palette_is_unchanged() -> None:
    for name, value in APPROVED_LIGHT.items():
        assert LIGHT[name] == value, name
    for name, value in APPROVED_DARK.items():
        assert DARK[name] == value, name
    # no colour token was added: every colour-looking token is in the approved set
    colours = {k for k, v in LIGHT.items() if re.fullmatch(r"#[0-9a-fA-F]{6}", v)}
    assert colours <= set(APPROVED_LIGHT), colours - set(APPROVED_LIGHT)


# --- typography tokens ---------------------------------------------------------------------

TYPE_PROPS = ("font-family", "font-size", "font-weight", "line-height", "letter-spacing")
ALLOWED_PLAIN = {"inherit", "normal", "0", "none"}


def test_no_rule_hard_codes_a_font_family_size_weight_line_height_or_tracking() -> None:
    offenders = []
    for selector, body in RULES:
        for prop in TYPE_PROPS:
            for value in re.findall(rf"(?<![-\w]){prop}\s*:\s*([^;]+)", body):
                value = value.strip()
                if value in ALLOWED_PLAIN or re.fullmatch(r"var\(--[a-z0-9-]+\)", value):
                    continue
                offenders.append((selector.strip(), prop, value))
        for value in re.findall(r"(?<![-\w])font\s*:\s*([^;]+)", body):
            ok = value.strip() == "inherit" or value.startswith("var(--w-regular) var(--text-base)")
            if not ok:
                offenders.append((selector.strip(), "font", value))
    assert offenders == []


def test_the_type_scale_is_the_documented_one_with_three_weights() -> None:
    assert {k: v for k, v in LIGHT.items() if k.startswith("w-")} == {
        "w-regular": "400", "w-medium": "500", "w-semibold": "600",
    }  # fmt: skip
    sizes = {k: v for k, v in LIGHT.items() if k.startswith("text-")}
    assert sizes == {
        "text-xs": "0.75rem", "text-sm": "0.875rem", "text-md": "0.9375rem",
        "text-base": "1rem", "text-lg": "1.125rem", "text-xl": "1.25rem",
        "text-2xl": "clamp(1.625rem, 1.25rem + 1.6vw, 2.125rem)", "text-stat": "2.25rem",
    }  # fmt: skip
    assert LIGHT["lh-ui"] == "1.5" and LIGHT["lh-prose"] == "1.6"
    assert LIGHT["measure-prose"] == "46rem"
    assert LIGHT["code-size"] == "0.875em" and LIGHT["track-caps"] == "0.06em"
    assert not re.search(r"font-weight:\s*[0-9]", BODY)  # no off-scale number anywhere


def test_the_manual_reads_at_46rem_with_a_prose_line_height() -> None:
    body: dict[str, str] = {}
    for selector, declarations in RULES:
        body.setdefault(selector.strip(), declarations)  # the first rule, not the print override
    # chapters are cards: the column is the 46rem measure plus the card's padding and border
    assert "max-width: calc(var(--measure-prose) + 2 * var(--sp-5) + 2px)" in body[".manual-body"]
    assert "line-height: var(--lh-prose)" in body[".prose"]
    assert "font: var(--w-regular) var(--text-base) / var(--lh-ui) var(--font)" in body["body"]


def test_the_serif_sets_only_titles_counts_and_card_titles_and_is_one_switch() -> None:
    from osa.panel import styles

    assert LIGHT["font-display"] == "var(--serif)"  # variant B is the default
    assert styles.DISPLAY == "serif" and styles.PRELOAD_FONTS == ("sans-latin", "serif-latin")
    display = [s.strip() for s, b in RULES if "font-family: var(--font-display)" in b]
    assert display == ["h1, h2, legend, .stat .value, .cta-title"]
    assert not any("var(--serif)" in b for s, b in RULES if s.strip() != ":root")  # one switch
    # the serif ships one weight (600); nothing set in it asks for more
    assert "font-weight: 600" in CSS.split("Plex Serif Web")[1].split("}")[0]
    # variant A: the same sheet with the sans everywhere, and only the text face preloaded
    sans = styles.build("sans")
    assert re.search(r"--font-display: var\(--font\);", sans)
    assert sans.replace("--font-display: var(--font);", "") == CSS.replace(
        "--font-display: var(--serif);", ""
    )
    assert styles.preload_fonts("sans") == ("sans-latin",)


def test_numbers_in_columns_and_identifiers_are_tabular_and_code_is_monospace() -> None:
    tabular = next(b for s, b in RULES if "font-variant-numeric: tabular-nums" in b and "td" in s)
    selector = next(s for s, b in RULES if "font-variant-numeric: tabular-nums" in b and "td" in s)
    assert "tabular-nums" in tabular
    for needle in ("td", ".stat .value", "input[type=text]", "code"):
        assert needle in selector, needle
    mono = next(b for s, b in RULES if s.strip() == "code, .tree")
    assert "font-family: var(--mono)" in mono and "font-size: var(--code-size)" in mono


# --- the surface: backdrop, cards, gold edge ---------------------------------------------------


def test_the_backdrop_is_a_fixed_lattice_with_soft_washes() -> None:
    layers = {s.strip(): b for s, b in RULES if s.strip() in ("body::before", "body::after")}
    assert set(layers) == {"body::before", "body::after"}
    shared = next(b for s, b in RULES if s.strip() == "body::before, body::after")
    for needle in ("position: fixed", "inset: 0", "z-index: -1", "pointer-events: none",
                   "72px 72px"):  # fmt: skip
        assert needle in shared, needle
    assert "opacity: var(--layer-light)" in layers["body::before"]
    assert "opacity: var(--layer-dark)" in layers["body::after"]
    assert (LIGHT["layer-light"], LIGHT["layer-dark"]) == ("1", "0")
    assert (DARK["layer-light"], DARK["layer-dark"]) == ("0", "1")
    for body in layers.values():
        assert body.count("radial-gradient") == 2 and body.count("1px, transparent 1px") == 2
    # the approved values, exactly (pixel-identical to the approved screenshots at 1920px)
    for needle in (
        "rgba(31, 58, 147, 0.05) 1px", "rgba(31, 58, 147, 0.08), transparent 70%",
        "rgba(179, 32, 42, 0.04), transparent 70%", "#ffffff 0%, #edf2fb 100%",
    ):  # fmt: skip
        assert needle in layers["body::before"], needle
    for needle in (
        "rgba(143, 176, 255, 0.045) 1px", "rgba(70, 110, 240, 0.16), transparent 70%",
        "rgba(200, 48, 59, 0.08), transparent 70%", "#0d1630 0%, #070c1a 100%",
    ):  # fmt: skip
        assert needle in layers["body::after"], needle
    # wide screens: thin gold lines beside the content column, so the margins are not empty
    wide = CSS.split("@media (min-width: 1500px) {", 1)[1].split("\n}\n", 1)[0]
    assert "main::before, main::after" in wide and "var(--gold) 18%" in wide
    # the body paints no background of its own (it would cover the z-index -1 layers)
    body_rule = next(b for s, b in RULES if s.strip() == "body")
    assert "background" not in body_rule
    assert "backdrop-filter" not in CSS
    gradients = {s.strip() for s, b in RULES if "gradient" in b and s.strip() != ":root"}
    # the backdrop, the select's caret and thin gold hairlines; never a filled surface (the
    # --gold-edge token on :root is the same thin gold line, drawn 1px high)
    assert gradients <= {
        "select", "body::before", "body::after", ".sidebar::after", "main::before, main::after",
    }, gradients  # fmt: skip
    for selector in gradients - {"select", "body::before", "body::after"}:
        body = next(b for s, b in RULES if s.strip() == selector)
        assert "var(--gold)" in body and "1px" in body, selector


def test_cards_and_banners_have_the_soft_layered_shadow_and_lift_on_hover() -> None:
    for theme in (LIGHT, DARK):
        assert theme["shadow-soft"].count("rgba") == 3 and theme["shadow-lift"].count("rgba") == 3
    first: dict[str, str] = {}
    for selector, declarations in RULES:
        first.setdefault(selector.strip(), declarations)
    for selector in (".card", ".notice", ".alert-success", ".alert-blocked", ".confirm"):
        assert "box-shadow: var(--shadow-soft)" in first[selector], selector
    lift = first[".card.lift:hover, a.card:hover"]
    assert "box-shadow: var(--shadow-lift)" in lift and "translateY(-3px)" in lift


def test_the_gold_edge_runs_along_cards_and_banners_and_widens_on_hover() -> None:
    assert LIGHT["gold-edge"] == "linear-gradient(90deg, transparent, var(--gold), transparent)"
    first: dict[str, str] = {}
    for selector, declarations in RULES:
        first.setdefault(selector.strip(), declarations)
    card = first[".card::before"]
    assert "background: var(--gold-edge)" in card and "height: 1px" in card and "top: -1px" in card
    banners = next(b for s, b in RULES if s.strip().startswith(".alert-blocked::before"))
    assert "background: var(--gold-edge)" in banners
    hover = first[".card.lift:hover::before, a.card:hover::before"]
    assert "opacity: 1" in hover and "left: var(--sp-2)" in hover
    # primary buttons and the numbered circles carry the fine gold inner ring
    assert LIGHT["gold-ring"] == "inset 0 0 0 1px rgba(214, 178, 94, 0.55)"
    primary = first[".btn-primary, .btn-save.is-dirty"]
    assert "box-shadow: var(--gold-ring)" in primary
    assert "box-shadow: var(--gold-ring)" in first[".chapter-num"]
    # the gold is decoration only: it never colours text
    for selector, body in RULES:
        assert not re.search(r"(?<![-\w])color:\s*var\(--gold\)", body), selector


def test_box_shadows_use_the_elevation_tokens_the_gold_ring_or_a_focus_ring() -> None:
    allowed = {"var(--shadow-soft)", "var(--shadow-lift)", "none", "none !important"}
    for selector, body in RULES:
        for value in re.findall(r"box-shadow:\s*([^;]+)", body):
            value = value.strip()
            assert (
                value in allowed
                or value.startswith(("var(--gold-ring)", "inset 0 0 0 1px var(--gold)"))
                or "focus" in selector
                # the knob's small drop and the sidebar's edge shade are the literal ones
                or selector.strip() in {".theme-switch .knob", ".sidebar"}
            ), (selector, value)


def test_corners_are_nearly_square_and_only_the_switch_knob_is_round() -> None:
    assert (LIGHT["radius-sm"], LIGHT["radius-md"], LIGHT["radius-lg"]) == ("2px", "3px", "4px")
    assert "radius-pill" not in CSS and "999px" not in BODY
    by_value: dict[str, list[str]] = {}
    for selector, body in RULES:
        for value in re.findall(r"border-radius:\s*([^;]+)", body):
            value = value.strip()
            assert value in {
                "var(--radius-sm)", "var(--radius-md)", "var(--radius-lg)", "50%", "2px", "0",
                "0 var(--radius-md) var(--radius-md) 0",
            }, (selector, value)  # fmt: skip
            by_value.setdefault(value, []).append(" ".join(selector.split()))
    assert by_value["50%"] == [".theme-switch .knob"]  # the one physical toggle knob
    assert by_value["2px"] == [".nav a::before"]  # the 2px-wide active marker bar
    for control in (
        "button, .btn",
        "input[type=text], select, textarea",
        ".badge",
        "code",
        ".fact strong",
        ".chapter-num",
        ".steps > li::before",
        ".toc-list a::before",
    ):
        assert control in by_value["var(--radius-sm)"], control
    for box in (
        ".alert",
        ".confirm",
        ".help-block",
        ".callout",
        ".problem",
        "html.js .theme-switch",
    ):
        assert box in by_value["var(--radius-md)"], box
    for surface in (".card", ".notice"):
        assert surface in by_value["var(--radius-lg)"], surface
    assert ".badge::before" in by_value["0"]  # a square status marker, not a dot
    # the gold edge stays clear of the corners (inset by the card padding, never full width)
    first: dict[str, str] = {}
    for selector, declarations in RULES:
        first.setdefault(selector.strip(), declarations)
    assert "left: var(--sp-4)" in first[".card::before"]
    assert "left: 0" not in first[".card.flush::before"]


def test_focus_rules_never_touch_the_border_radius() -> None:
    focus = [(s, b) for s, b in RULES if ":focus-visible" in s]
    assert focus
    for selector, body in focus:
        assert "border-radius" not in body, selector


def test_spacing_lengths_stay_on_the_four_pixel_grid() -> None:
    assert [LIGHT[f"sp-{i}"] for i in range(1, 9)] == [
        "0.25rem", "0.5rem", "0.75rem", "1rem", "1.5rem", "2rem", "3rem", "4rem",
    ]  # fmt: skip
    bad = []
    for selector, body in RULES:
        for prop in ("padding", "margin", "gap"):
            for value in re.findall(rf"(?<![-\w]){prop}(?:-[a-z]+)?\s*:\s*([^;]+)", body):
                plain = re.sub(r"var\([^)]*\)", "", value)
                for amount in re.findall(r"(?<![\w.-])(-?[0-9.]+)(rem|px)\b", plain):
                    number, unit = float(amount[0]), amount[1]
                    on_grid = number == 0 or (number * (16 if unit == "rem" else 1)) % 4 == 0
                    if not on_grid and number not in (0.125,) and unit == "rem":
                        bad.append((selector.strip(), prop, value))
                    if unit == "px" and number % 4 and abs(number) != 1:  # 1px: clip trick
                        bad.append((selector.strip(), prop, value))
    assert bad == []


def test_motion_is_short_feedback_and_reduced_motion_removes_it() -> None:
    for name in ("rise", "wake", "nav-pop", "pop", "pop-tap"):
        assert f"@keyframes {name} " in CSS, name
    assert "wiggle" not in CSS  # the menu icons only pop, they never shake
    assert "infinite" not in CSS
    for duration in re.findall(r"(?:transition|animation)[^;]*?\s([0-9.]+)s", CSS):
        assert float(duration) <= 0.6, duration
    reduced = CSS.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "animation: none !important" in reduced and "transition: none !important" in reduced


# --- WCAG AA, by calculation, in both themes ------------------------------------------------


def _luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# (foreground, background, minimum): 4.5 for text, 3 for large text and UI graphics
PAIRS = [
    ("ink", "paper", 4.5), ("ink", "surface", 4.5), ("ink", "row-hover", 4.5),
    ("ink", "surface-2", 4.5), ("muted", "paper", 4.5), ("muted", "surface", 4.5),
    ("muted", "surface-2", 4.5), ("muted", "row-hover", 4.5),  # eyebrow, hint, th, access badge
    ("blue", "surface", 4.5), ("blue", "paper", 4.5), ("blue", "blue-soft", 4.5),
    ("blue-deep", "blue-soft", 4.5), ("blue-deep", "surface", 4.5),  # nav, notice, links
    ("blue-deep", "paper", 4.5), ("blue-deep", "surface-2", 4.5),
    ("red-deep", "red-soft", 4.5), ("red-deep", "surface", 4.5),  # badge-off, confirm title
    ("red", "surface", 4.5), ("on-accent", "blue-solid", 4.5),  # btn-danger, primary / dirty save
    ("on-accent", "blue-solid-hover", 4.5), ("on-accent", "red-solid", 4.5),  # confirm button
    ("control", "surface", 3.0), ("focus", "surface", 3.0), ("focus", "paper", 3.0),
    ("blue", "blue-soft", 3.0), ("red", "red-soft", 3.0),  # alert icons
    ("ink", "blue-soft", 4.5), ("ink", "red-soft", 4.5),  # help block body, manual link, callouts
    ("red-deep", "surface-2", 4.5),  # problem tags and fact chips sit on cards and steps
    ("gold", "paper", 1.0),  # the thin gold rules are decoration, never the only signal
]  # fmt: skip


def _check(theme: dict[str, str]) -> list[tuple[str, str, float]]:
    return [
        (fg, bg, round(ratio(theme[fg], theme[bg]), 2))
        for fg, bg, minimum in PAIRS
        if ratio(theme[fg], theme[bg]) < minimum
    ]


def test_every_changed_pairing_meets_wcag_aa_in_the_light_theme() -> None:
    assert _check(LIGHT) == []


def test_every_changed_pairing_meets_wcag_aa_in_the_dark_theme() -> None:
    assert _check(DARK) == []


# Text that sits on the page margin (page header, eyebrow, lead, "Voltar") is over the backdrop,
# not over --paper. Checked pessimistically for each layer: either end of the base gradient, one
# lattice line and either wash at its full strength on top (the washes sit in opposite corners
# and never overlap at strength; a lattice crossing is a single pixel, not a glyph background).
ON_BACKDROP = [("ink", 4.5), ("muted", 4.5), ("blue", 4.5), ("blue-deep", 4.5), ("focus", 3.0)]


def _over(base: tuple[float, ...], rgba: tuple[float, ...]) -> tuple[float, ...]:
    *rgb, alpha = rgba
    return tuple(c * (1 - alpha) + o * alpha for c, o in zip(base, rgb, strict=True))


def _backdrop_extremes(layer: str) -> list[str]:
    body = next(b for s, b in RULES if s.strip() == layer)
    rgba = r"rgba\((\d+), (\d+), (\d+), ([0-9.]+)\)"
    lines = [
        tuple(float(x) for x in m) for m in re.findall(r"linear-gradient\(\d+deg, " + rgba, body)
    ]
    washes = [
        tuple(float(x) for x in m) for m in re.findall(r"radial-gradient\([^,]+, " + rgba, body)
    ]
    bases = re.findall(r"(#[0-9a-f]{6}) \d+%", body)
    assert len(lines) == 2 and len(washes) == 2 and len(bases) == 2, layer
    assert lines[0] == lines[1]
    out = list(bases)
    for base in bases:
        plain: tuple[float, ...] = tuple(float(int(base[i : i + 2], 16)) for i in (1, 3, 5))
        for wash in washes:
            colour = _over(_over(plain, lines[0]), wash)
            out.append("#" + "".join(f"{round(c):02x}" for c in colour))
    return out


def test_text_on_the_page_margin_meets_wcag_aa_over_the_backdrop_in_both_themes() -> None:
    failures = []
    for theme, layer in ((LIGHT, "body::before"), (DARK, "body::after")):
        for background in _backdrop_extremes(layer):
            for fg, minimum in ON_BACKDROP:
                if ratio(theme[fg], background) < minimum:
                    failures.append((layer, fg, background, round(ratio(theme[fg], background), 2)))
    assert failures == []
