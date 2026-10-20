"""Stylesheet of the local panel, served by ``/panel.css``.

One file on purpose: the CSP is ``default-src 'self'; style-src 'self'``, so there is no
inline ``<style>``, no ``style=""`` attribute and no external reference (the security tests
also refuse any ``http://``, ``https://`` or ``@import`` in this text). The typefaces (IBM
Plex Sans for text and controls, IBM Plex Serif for titles and counts, IBM Plex Mono for
folders and codes; one switch, ``DISPLAY``, at the end) are self-hosted files under
``/fonts/`` (``osa.panel.assets``): with no ``font-src`` the CSP falls back to
``default-src 'self'``, so the policy did not change. The files are IBM's own web builds,
unchanged; the ``@font-face`` families here still use neutral aliases ("Panel Sans", "Panel
Serif", "Panel Mono" and their "... Fallback" faces), so no local font can collide with them.
Palette: white and pale-blue surfaces (a deep navy ink in the dark theme), blue as the
primary color, red for destructive actions, errors and "inativo", and gold only as thin
details (1px rules and gradient edges, the active-nav marker, the focus inner line, the inner
ring of primary buttons). Both themes are CSS custom
properties: ``:root`` is light; the same dark values apply under ``[data-theme="dark"]`` and,
before any script runs or without one, under ``prefers-color-scheme: dark`` unless the page
is pinned to light.
Typography, spacing and radii are tokens too (``--text-*``, ``--w-*``, ``--lh-*``,
``--track-*``, ``--sp-*``, ``--radius-*``); a test refuses a hard-coded font family, size,
weight, line height or tracking anywhere below the token block. Surfaces are cards: white (navy
in the dark theme) with a hairline border, a soft layered shadow (``--shadow-soft``, deeper
``--shadow-lift`` when a linked card lifts on hover) and a thin gold gradient edge along the top
that widens on hover; primary buttons and numbered squares carry a fine gold inner ring. Corners
are nearly square (2px controls, badges and chips; 3px alerts and small boxes; 4px cards); the
theme switch knob is the only round shape. Behind everything, a
fixed backdrop (soft gradient washes and a hairline lattice, one layer per theme, cross-faded).
Layout is a fixed left sidebar from 900px up and a compact top bar below it. CSS motion is
short and runs once (page blocks pop in with a small stagger, linked cards lift on hover, menu
icons pop on hover, focus, press and when their page opens: scale only, no rotation, no
glow), and all of it is switched
off under ``prefers-reduced-motion``. The
``lt-*`` classes repaint the Lottie animations (mounted by ``/motion.js``) with these same
tokens, so the animation files carry no theme of their own.
"""

from typing import Final

from osa.panel.assets import font_path

_DARK_TOKENS: Final = """\
  --paper: #0a1020;
  --surface: #111a31;
  --surface-2: #0d1529;
  --row-hover: #15203c;
  --ink: #e9edf7;
  --muted: #a3adc6;
  --hairline: #222d49;
  --hairline-strong: #33416a;
  --control: #7a88ad;
  --blue: #8fb0ff;
  --blue-solid: #3a5fd9;
  --blue-solid-hover: #2a4bb8;
  --blue-deep: #c3d3ff;
  --blue-soft: #17264b;
  --blue-line: #2f4580;
  --red: #ff8f97;
  --red-solid: #c8303b;
  --red-deep: #ffb4b9;
  --red-soft: #2b1420;
  --red-line: #6b2a38;
  --gold: #d6b25e;
  --focus: #a9c2ff;
  --link-line: rgba(143, 176, 255, 0.4);
  --knob: #dfe7ff;
  --knob-x: 1.75rem;
  --sun-color: #a3adc6;
  --moon-color: #14275f;
  --knob-ink: #14275f;
  --shadow-soft: 0 1px 2px rgba(0, 0, 0, 0.35), 0 14px 34px -14px rgba(0, 0, 0, 0.65),
    0 36px 70px -36px rgba(30, 60, 160, 0.3);
  --shadow-lift: 0 2px 4px rgba(0, 0, 0, 0.4), 0 22px 44px -16px rgba(0, 0, 0, 0.75),
    0 40px 80px -36px rgba(60, 100, 220, 0.4);
  --layer-light: 0;
  --layer-dark: 1;
  color-scheme: dark;
"""

# Exactly the character ranges of IBM's own subsets ("Latin1" and "Latin2" in
# ``fonts/split/woff2`` of the ``@ibm/plex-*`` packages; checked against each file's cmap). The
# Latin2 file is only fetched when a page uses a character of its range.
_LATIN1: Final = """\
U+0020-007E, U+00A0-00FF, U+0131, U+0152-0153, U+02C6, U+02DA, U+02DC, U+2013-2014,
    U+2018-201A, U+201C-201E, U+2020-2022, U+2026, U+2030, U+2039-203A, U+2044, U+20AC, U+2122,
    U+2212, U+FB01-FB02"""
_LATIN2: Final = """\
U+0100-0101, U+0104-0130, U+0132-0151, U+0154-017F, U+018F, U+0192, U+01A0-01A1,
    U+01AF-01B0, U+01FA-01FF, U+0218-021B, U+0237, U+0259, U+1E80-1E85, U+1E9E, U+20A1, U+20A4,
    U+20A6, U+20A8-20AA, U+20AD-20AE, U+20B1-20B2, U+20B4-20B5, U+20B8-20BA, U+20BD, U+20BF"""
_RANGES: Final = {"latin1": _LATIN1, "latin2": _LATIN2}


def _face(family: str, weight: int, subset: str) -> str:
    """One self-hosted face: one weight of one family, one IBM subset, by its hashed URL."""
    key = f"{family.lower()}-{weight}-{subset}"
    return (
        "@font-face {\n"
        f'  font-family: "Panel {family}"; font-style: normal; font-weight: {weight};\n'
        f'  font-display: swap; src: url("{font_path(key)}") format("woff2");\n'
        f"  unicode-range: {_RANGES[subset]};\n"
        "}\n"
    )


# The sans ships three static weights (IBM publishes no variable web build); the serif 600 only,
# the mono 400 only (Latin1 only: folders and codes).
_WEB_FACES: Final = "".join(
    [
        *(_face("Sans", w, s) for w in (400, 500, 600) for s in ("latin1", "latin2")),
        *(_face("Serif", 600, s) for s in ("latin1", "latin2")),
        _face("Mono", 400, "latin1"),
    ]
)

_FACES: Final = f"""\
/* ---------- fonts: self-hosted, hashed file names, metric-compatible fallbacks ---------- */
{_WEB_FACES}/* Same metrics as each web face (advance widths and hhea ascent/descent of the font
   files against the local face; the sans from its 400 face), so the swap moves nothing. */
@font-face {{
  font-family: "Panel Sans Fallback"; src: local("Arial"), local("Helvetica");
  size-adjust: 101.1%; ascent-override: 101.4%; descent-override: 27.2%; line-gap-override: 0%;
}}
@font-face {{
  font-family: "Panel Serif Fallback"; src: local("Georgia"), local("Times New Roman");
  size-adjust: 109.4%; ascent-override: 93.7%; descent-override: 25.1%; line-gap-override: 0%;
}}
@font-face {{
  font-family: "Panel Mono Fallback"; src: local("Courier New"), local("Courier");
  size-adjust: 100%; ascent-override: 102.5%; descent-override: 27.5%; line-gap-override: 0%;
}}

"""

_CSS: Final = """\
/* ---------- tokens ---------- */
:root {
  --paper: #f6f8fd;
  --surface: #ffffff;
  --surface-2: #f4f7fc;
  --row-hover: #f8faff;
  --ink: #12172b;
  --muted: #566075;
  --hairline: #e3e7f0;
  --hairline-strong: #cfd6e6;
  --control: #7a8398;
  --blue: #1f3a93;
  --blue-solid: #1f3a93;
  --blue-solid-hover: #14275f;
  --blue-deep: #14275f;
  --blue-soft: #eaf0fb;
  --blue-line: #c5d3f2;
  --red: #b3202a;
  --red-solid: #b3202a;
  --red-deep: #8e1820;
  --red-soft: #fbecec;
  --red-line: #e7b8bb;
  --gold: #b8903a;
  --focus: #1f3a93;
  --on-accent: #ffffff;
  --link-line: rgba(31, 58, 147, 0.35);
  --knob: #ffffff;
  --knob-x: 0rem;
  --sun-color: #1f3a93;
  --moon-color: #566075;
  --knob-ink: #1f3a93;
  --anchor-offset: 1rem;
  /* which backdrop layer shows (body::before light, body::after dark); they cross-fade */
  --layer-light: 1;
  --layer-dark: 0;
  /* elevation: a soft layered shadow for cards and banners, a deeper one for the hover lift */
  --shadow-soft: 0 1px 2px rgba(18, 23, 43, 0.05), 0 12px 32px -14px rgba(18, 23, 43, 0.16),
    0 32px 64px -34px rgba(31, 58, 147, 0.18);
  --shadow-lift: 0 2px 4px rgba(18, 23, 43, 0.06), 0 22px 44px -16px rgba(31, 58, 147, 0.3),
    0 40px 80px -36px rgba(31, 58, 147, 0.26);
  /* the fine gold edge: along the top of cards and banners, inside primary buttons */
  --gold-edge: linear-gradient(90deg, transparent, var(--gold), transparent);
  --gold-ring: inset 0 0 0 1px color-mix(in srgb, var(--gold) 55%, transparent);
  --ease: cubic-bezier(0.2, 0.7, 0.2, 1);
  --fade: background-color 0.35s var(--ease), color 0.35s var(--ease),
    border-color 0.35s var(--ease);

  /* typography: families. Neutral aliases (no upstream name, see ADR 0009) that never collide
     with a locally installed font. */
  --font: "Panel Sans", "Panel Sans Fallback", "Segoe UI", Roboto, Helvetica, Arial,
    sans-serif;
  --serif: "Panel Serif", "Panel Serif Fallback", Georgia, "Times New Roman", serif;
  --mono: "Panel Mono", "Panel Mono Fallback", ui-monospace, Menlo, Consolas, monospace;
  /* titles, counts and card titles: the one switch between the two type variants */
  --font-display: /*DISPLAY*/;
  /* scale (rem, base 16px) */
  --text-xs: 0.75rem;
  --text-sm: 0.875rem;
  --text-md: 0.9375rem;
  --text-base: 1rem;
  --text-lg: 1.125rem;
  --text-xl: 1.25rem;
  --text-2xl: clamp(1.625rem, 1.25rem + 1.6vw, 2.125rem);
  --text-stat: 2.25rem;
  --code-size: 0.875em;
  /* three weights (the serif ships 600 only, the mono 400 only) */
  --w-regular: 400;
  --w-medium: 500;
  --w-semibold: 600;
  /* line heights */
  --lh-none: 1;
  --lh-tight: 1.15;
  --lh-snug: 1.25;
  --lh-ui: 1.5;
  --lh-prose: 1.6;
  /* tracking */
  --track-tight: -0.015em;
  --track-snug: -0.01em;
  --track-badge: 0.01em;
  --track-caps: 0.06em;
  /* measure */
  --measure-prose: 46rem;
  --measure-lead: 42rem;

  /* spacing: a 4px grid (0.25rem steps, doubling from 1rem) */
  --sp-1: 0.25rem;
  --sp-2: 0.5rem;
  --sp-3: 0.75rem;
  --sp-4: 1rem;
  --sp-5: 1.5rem;
  --sp-6: 2rem;
  --sp-7: 3rem;
  --sp-8: 4rem;
  /* radii: nearly square. 2px controls, badges and chips; 3px alerts and small boxes; 4px cards,
     panels and banners. Nothing is fully round except the theme switch knob. */
  --radius-sm: 2px;
  --radius-md: 3px;
  --radius-lg: 4px;
  --control-h: 2.5rem;
  --control-h-sm: 2rem;
  color-scheme: light;
}
:root[data-theme="dark"] {
/*DARK*/}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
/*DARK*/  }
}

/* ---------- base ---------- */
*, *::before, *::after { box-sizing: border-box; }
html {
  -webkit-text-size-adjust: 100%;
  scroll-behavior: smooth;
  background-color: var(--paper);
  transition: background-color 0.35s var(--ease);
}
body {
  margin: 0;
  min-height: 100vh;
  font: var(--w-regular) var(--text-base) / var(--lh-ui) var(--font);
  color: var(--ink);
  -webkit-font-smoothing: antialiased;
  transition: var(--fade);
}
/* Two fixed backdrop layers that cross-fade with the theme: a very soft gradient wash plus a
   hairline lattice. Surfaces are opaque, so the lattice only shows in the margins. The body
   has no background of its own (it would paint over these z-index -1 layers); --paper on
   <html> stays underneath as the base colour. */
body::before, body::after {
  content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none;
  background-size: 72px 72px, 72px 72px, auto, auto, auto;
  transition: opacity 0.45s var(--ease);
}
body::before {
  opacity: var(--layer-light);
  background-image:
    linear-gradient(90deg, rgba(31, 58, 147, 0.05) 1px, transparent 1px),
    linear-gradient(0deg, rgba(31, 58, 147, 0.05) 1px, transparent 1px),
    radial-gradient(64rem 30rem at 90% -10%, rgba(31, 58, 147, 0.08), transparent 70%),
    radial-gradient(48rem 26rem at -8% 110%, rgba(179, 32, 42, 0.04), transparent 70%),
    linear-gradient(180deg, #ffffff 0%, #edf2fb 100%);
}
body::after {
  opacity: var(--layer-dark);
  background-image:
    linear-gradient(90deg, rgba(143, 176, 255, 0.045) 1px, transparent 1px),
    linear-gradient(0deg, rgba(143, 176, 255, 0.045) 1px, transparent 1px),
    radial-gradient(64rem 30rem at 90% -10%, rgba(70, 110, 240, 0.16), transparent 70%),
    radial-gradient(48rem 26rem at -8% 110%, rgba(200, 48, 59, 0.08), transparent 70%),
    linear-gradient(180deg, #0d1630 0%, #070c1a 100%);
}
h1, h2, h3 {
  margin: 0;
  color: var(--blue-deep);
  line-height: var(--lh-snug);
  text-wrap: balance;
  transition: color 0.35s var(--ease);
}
h1, h2, legend, .stat .value, .cta-title { font-family: var(--font-display); }
h1 { font-size: var(--text-2xl); font-weight: var(--w-semibold); line-height: var(--lh-tight);
  letter-spacing: var(--track-tight); }
h2 { font-size: var(--text-xl); font-weight: var(--w-semibold); letter-spacing: var(--track-snug); }
h3 { font-size: var(--text-lg); font-weight: var(--w-semibold); }
p { margin: 0; }
a { color: var(--blue); text-decoration-color: var(--link-line); text-underline-offset: 3px;
  transition: color 0.15s var(--ease), text-decoration-color 0.15s var(--ease); }
a:hover { color: var(--blue-deep); text-decoration-color: var(--gold); }
code, .tree {
  font-family: var(--mono);
  font-size: var(--code-size);
  font-weight: var(--w-regular);
  line-height: var(--lh-ui);
}
code {
  padding: 0.1em 0.4em;
  color: var(--blue-deep);
  background: var(--blue-soft);
  border-radius: var(--radius-sm);
  overflow-wrap: anywhere;
  transition: var(--fade);
}
strong { font-weight: var(--w-semibold); }
.muted { color: var(--muted); }
.skip {
  position: absolute; left: var(--sp-4); top: -4rem; z-index: 40;
  padding: var(--sp-2) var(--sp-4); border-radius: var(--radius-sm);
  background: var(--blue-solid); color: var(--on-accent); text-decoration: none;
  transition: top 0.15s var(--ease);
}
.skip:focus { top: var(--sp-3); }
.icon { width: 1.25rem; height: 1.25rem; flex: none; }
/* numbers that are compared down a column or read as identifiers keep a fixed width */
td, .stat .value, .fact, input[type=text], textarea, .chapter-num, .steps > li::before,
.toc-list a::before, code, .tree, .badge { font-variant-numeric: tabular-nums; }

/* ---------- focus (the border radius of the element is never touched) ---------- */
:focus { outline: none; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
input:focus-visible, select:focus-visible, textarea:focus-visible {
  outline: none;
  border-color: var(--focus);
  box-shadow: inset 0 0 0 1px var(--gold), 0 0 0 3px var(--blue-soft), 0 0 0 4px var(--focus);
}
.btn:focus-visible, button:focus-visible { outline-offset: 3px; }
.btn-confirm:focus-visible { outline-color: var(--red); }

/* ---------- shell: sidebar on wide screens, compact top bar below ---------- */
.shell { display: block; }
.sidebar {
  position: relative; z-index: 10;
  display: grid; grid-template-columns: 1fr auto; align-items: center;
  grid-template-areas: "brand foot" "nav nav"; gap: var(--sp-2) var(--sp-4);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface);
  border-bottom: 1px solid var(--hairline);
  transition: var(--fade);
}
.sidebar::after {
  content: ""; position: absolute; left: 0; right: 0; bottom: -1px; height: 1px;
  background: linear-gradient(90deg, transparent, var(--gold) 20%, var(--gold) 80%, transparent);
  opacity: 0.55; pointer-events: none;
}
.brand {
  grid-area: brand;
  display: inline-flex; align-items: center; gap: var(--sp-3);
  color: var(--blue-deep); text-decoration: none;
}
.brand:hover { color: var(--blue-deep); }
.brand svg {
  width: 2rem; height: 2rem; flex: none; transform-origin: 50% 60%;
  /* --mx/--my/--tilt/--lift are set by /motion.js while the pointer is over the mark */
  transform: translate(var(--mx, 0px), var(--my, 0px)) rotate(var(--tilt, 0deg))
    scale(var(--lift, 1));
}
.brand.brand-active svg { will-change: transform; } /* only during the interaction */
.brand .shield-fill { fill: var(--blue-solid); transition: fill 0.35s var(--ease); }
.brand .shield-line { stroke: var(--gold); }
.brand .shield-cross { stroke: var(--on-accent); }
.brand .shield-drop { fill: var(--red-solid); }
/* Without the script (or if it fails to load) the mark still pops softly, once, on hover,
   keyboard focus and press. With the script (.live) a spring does it, following the mouse. */
.brand:not(.live):hover svg, .brand:not(.live):focus-visible svg { animation: pop 0.52s linear; }
.brand:not(.live):active svg { animation: pop-tap 0.42s linear; }
.brand-text { display: flex; flex-direction: column; line-height: var(--lh-tight); }
.brand-name { font-weight: var(--w-semibold); font-size: var(--text-base);
  letter-spacing: var(--track-snug); }
.brand-sub { font-size: var(--text-xs); font-weight: var(--w-semibold);
  letter-spacing: var(--track-caps); text-transform: uppercase; color: var(--muted); }
.nav { grid-area: nav; display: flex; flex-wrap: wrap; gap: var(--sp-1); }
.nav a {
  position: relative; display: flex; align-items: center; gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-3); border-radius: var(--radius-sm);
  font-size: var(--text-md); font-weight: var(--w-medium); color: var(--muted);
  text-decoration: none; transition: var(--fade), transform 0.2s var(--ease);
}
.nav-ico { position: relative; flex: none; display: inline-flex; }
.nav a .icon { width: 1.25rem; height: 1.25rem; transition: color 0.2s var(--ease); }
.nav a::after {
  content: ""; position: absolute; left: var(--sp-3); right: var(--sp-3); bottom: var(--sp-1);
  height: 1px; background: var(--gold); transform: scaleX(0); transform-origin: left;
  transition: transform 0.3s var(--ease);
}
.nav a:hover { color: var(--blue-deep); background: var(--blue-soft); }
.nav a:hover::after { transform: scaleX(1); }
.nav a:hover .icon { color: var(--blue); }
.nav a[aria-current="page"] { color: var(--blue-deep); background: var(--blue-soft); }
.nav a[aria-current="page"] .icon { color: var(--blue); }
.nav a[aria-current="page"]::after { transform: scaleX(1); }
.side-foot { grid-area: foot; display: flex; align-items: center; gap: var(--sp-3); }
.side-label { display: none; font-size: var(--text-xs); font-weight: var(--w-semibold);
  letter-spacing: var(--track-caps); text-transform: uppercase; color: var(--muted); }
.access-badge {
  display: none; align-items: center; gap: var(--sp-2);
  font-size: var(--text-sm); line-height: var(--lh-snug); color: var(--muted);
}
.access-badge .icon { width: 1rem; height: 1rem; color: var(--blue); }
.access-badge code { padding: 0; background: none; color: inherit; display: block; }
.access-badge .sep { display: none; }

/* ---------- theme switch: sun and moon on a track, the knob sits on the active one ---------- */
.theme-switch { display: none; }
html.js .theme-switch {
  position: relative; display: inline-block; flex: none;
  width: 3.75rem; height: 2rem; min-height: 0; padding: 0;
  background: var(--blue-soft); border: 1px solid var(--control);
  border-radius: var(--radius-md);
  transition: var(--fade), box-shadow 0.2s var(--ease);
}
html.js .theme-switch:hover { background: var(--blue-soft); border-color: var(--blue); }
html.js .theme-switch:active { transform: none; }
.theme-switch .knob {
  position: absolute; top: 0.1875rem; left: 0.1875rem; width: 1.5rem; height: 1.5rem;
  border-radius: 50%; background: var(--knob); border: 1px solid var(--gold);
  box-shadow: 0 2px 6px -1px rgba(0, 0, 0, 0.35);
  transform: translateX(var(--knob-x));
  transition: transform 0.35s var(--ease), background-color 0.35s var(--ease);
}
.knob-art { position: absolute; inset: 0.1875rem; color: var(--knob-ink); }
.knob-art .lottie-layer { inset: 0; }
.theme-switch .icon {
  position: absolute; top: 50%; width: 1rem; height: 1rem; margin-top: -0.5rem;
  transition: color 0.35s var(--ease);
}
.theme-switch .sun { left: 0.5rem; color: var(--sun-color); }
.theme-switch .moon { right: 0.5rem; color: var(--moon-color); }

/* ---------- layout ---------- */
.page { display: flex; flex-direction: column; min-height: 100vh; min-width: 0; }
main {
  position: relative; width: 100%; max-width: 84rem; margin: 0 auto; flex: 1;
  padding: var(--sp-5) var(--sp-4) var(--sp-7);
  display: flex; flex-direction: column; gap: var(--sp-5);
}
.footer {
  max-width: 84rem; width: 100%; margin: 0 auto; padding: var(--sp-4) var(--sp-4) var(--sp-5);
  font-size: var(--text-sm); color: var(--muted); position: relative;
}
.footer::before { content: ""; position: absolute; left: var(--sp-4); top: 0; width: var(--sp-5);
  height: 1px; background: var(--gold); }
.footer .icon { width: 1rem; height: 1rem; vertical-align: -0.2em; margin-right: var(--sp-2);
  color: var(--blue); }
.footer code { padding: 0; background: none; color: inherit; }
/* editorial header: eyebrow, title, one line; a short gold rule closes it */
.page-head {
  position: relative; display: flex; flex-direction: column; gap: var(--sp-2);
  padding-bottom: var(--sp-4); border-bottom: 1px solid var(--hairline);
}
.page-head::after {
  content: ""; position: absolute; left: 0; bottom: -1px; width: var(--sp-7); height: 1px;
  background: var(--gold);
}
.page-head h1 { max-width: var(--measure-lead); }
.eyebrow {
  font-size: var(--text-xs); font-weight: var(--w-semibold); letter-spacing: var(--track-caps);
  text-transform: uppercase; color: var(--muted);
}
.lead { color: var(--muted); font-size: var(--text-base); max-width: var(--measure-lead); }
.back {
  display: inline-flex; align-items: center; gap: var(--sp-1); align-self: flex-start;
  font-size: var(--text-md); font-weight: var(--w-medium); text-decoration: none;
  color: var(--muted);
}
.back:hover { color: var(--blue); }
.grid { display: grid; gap: var(--sp-4);
  grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); }
.split { display: grid; gap: var(--sp-5); align-items: start;
  grid-template-columns: minmax(0, 1fr); }
.stack { display: flex; flex-direction: column; gap: var(--sp-5); }

/* ---------- cards: hairline border, soft layered shadow, a thin gold edge along the top ------ */
.card {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-soft);
  padding: var(--sp-4);
  transition: var(--fade), transform 0.25s var(--ease), box-shadow 0.25s var(--ease);
}
.card::before {
  content: ""; position: absolute; left: var(--sp-4); right: var(--sp-4); top: -1px; height: 1px;
  background: var(--gold-edge); opacity: 0.6; pointer-events: none;
  transition: opacity 0.3s var(--ease), left 0.3s var(--ease), right 0.3s var(--ease);
}
.card-head { display: flex; align-items: center; justify-content: space-between;
  gap: var(--sp-4); flex-wrap: wrap; margin-bottom: var(--sp-4); }
.card-head h2 { margin: 0; }
.card.flush { padding: 0; overflow: hidden; }
.card.flush::before { top: 0; z-index: 1; }
/* linked cards (and the home "Modelo efetivo") lift on hover; the gold edge widens */
.card.lift:hover, a.card:hover {
  transform: translateY(-3px); box-shadow: var(--shadow-lift);
  border-color: var(--hairline-strong);
}
.card.lift:hover::before, a.card:hover::before { opacity: 1; left: var(--sp-2);
  right: var(--sp-2); }
a.card { text-decoration: none; color: inherit; display: block; }
.section-label {
  font-size: var(--text-xs); font-weight: var(--w-semibold); letter-spacing: var(--track-caps);
  text-transform: uppercase; color: var(--muted); margin-bottom: var(--sp-2);
}
.divided { border-top: 1px solid var(--hairline); padding-top: var(--sp-4);
  margin-top: var(--sp-4); }

/* home: the four counts, each its own card that opens its section */
a.card.stat { display: flex; flex-direction: column; gap: var(--sp-1); }
.stat .label { font-size: var(--text-sm); font-weight: var(--w-medium); color: var(--muted); }
.stat .value { font-size: var(--text-stat); font-weight: var(--w-semibold); color: var(--blue-deep);
  letter-spacing: var(--track-tight); line-height: var(--lh-none); }
.stat .more { margin-top: var(--sp-2); font-size: var(--text-sm); font-weight: var(--w-medium);
  transition: transform 0.25s var(--ease); }
a.card:hover .more { transform: translateX(4px); }
.stats-block { display: flex; flex-direction: column; gap: var(--sp-3); }
.next-step { color: var(--muted); }
.next-step a { font-weight: var(--w-semibold); }

/* ---------- alerts and banners ---------- */
.alert {
  display: flex; gap: var(--sp-3); align-items: flex-start;
  padding: var(--sp-3) var(--sp-4); border-radius: var(--radius-md);
  border: 1px solid; background: var(--surface); transition: var(--fade);
}
.alert .icon { margin-top: 0.125rem; }
.alert p + p { margin-top: var(--sp-1); }
.alert-title { display: block; margin-bottom: 0.125rem; }
.alert-note { color: inherit; font-size: var(--text-sm); opacity: 0.85; }
.alert-error { background: var(--red-soft); border-color: var(--red-line); color: var(--red-deep); }
.alert-error .icon { color: var(--red); }
.alert-info { background: var(--blue-soft); border-color: var(--blue-line);
  color: var(--blue-deep); }
.alert-info .icon { color: var(--blue); }
.alert-blocked { background: var(--surface); border-color: var(--red-line); color: var(--ink);
  box-shadow: var(--shadow-soft); position: relative; }
.alert-success { background: var(--surface); border-color: var(--blue-line); color: var(--ink);
  box-shadow: var(--shadow-soft); position: relative; align-items: center;
  font-weight: var(--w-medium); }
.alert-blocked::before, .alert-success::before, .confirm::before, .notice::before,
.callout-warn::before {
  content: ""; position: absolute; left: var(--sp-4); right: var(--sp-4); top: -1px; height: 1px;
  background: var(--gold-edge); pointer-events: none;
}
.alert-art { position: relative; flex: none; display: grid; place-items: center;
  width: 2.5rem; height: 2.5rem; color: var(--blue); }
.alert .alert-art > .icon { width: 2rem; height: 2rem; margin: 0; color: var(--blue); }
.alert-art.lottie-on > .icon { opacity: 0; }
.alert-blocked .icon { color: var(--red); }
.alert-blocked .alert-title { color: var(--red-deep); }
.alert-blocked code { background: var(--red-soft); color: var(--red-deep); }
/* confirmation of a destructive action: same page, nothing has happened yet */
.confirm {
  position: relative; display: flex; flex-direction: column; gap: var(--sp-3);
  padding: var(--sp-4); border-radius: var(--radius-md);
  background: var(--surface); border: 1px solid var(--red-line); border-left: 3px solid var(--red);
  box-shadow: var(--shadow-soft);
}
.confirm-title { font-size: var(--text-lg); font-weight: var(--w-semibold); color: var(--red-deep);
  line-height: var(--lh-snug); overflow-wrap: anywhere; }
.confirm p { color: var(--ink); }
.confirm-actions { display: flex; gap: var(--sp-3); flex-wrap: wrap; align-items: center; }
.confirm-actions form { margin: 0; }
/* "Acesso somente local": informative, not an alarm (blue), with the card's shadow and edge */
.notice {
  position: relative; display: flex; gap: var(--sp-4); align-items: flex-start;
  padding: var(--sp-4); border-radius: var(--radius-lg);
  background: var(--blue-soft); border: 1px solid var(--blue-line); color: var(--blue-deep);
  box-shadow: var(--shadow-soft); transition: var(--fade);
}
.notice .icon { width: 1.5rem; height: 1.5rem; color: var(--blue); margin-top: 0.125rem; }
.notice .alert-title { font-size: var(--text-base); color: var(--blue-deep); }
.notice code { background: var(--surface); color: var(--blue-deep); }
.dependency { background: var(--surface-2); border: 1px dashed var(--hairline-strong);
  border-radius: var(--radius-md); padding: var(--sp-4); color: var(--ink); }
.dependency a { font-weight: var(--w-semibold); }

/* ---------- buttons: 2px corners ---------- */
button, .btn {
  position: relative;
  appearance: none; -webkit-appearance: none;
  display: inline-flex; align-items: center; justify-content: center; gap: var(--sp-2);
  min-height: var(--control-h); padding: var(--sp-2) var(--sp-4);
  font: inherit; font-size: var(--text-md); font-weight: var(--w-semibold);
  line-height: var(--lh-snug); text-decoration: none; white-space: nowrap;
  color: var(--blue); background: var(--surface);
  border: 1px solid var(--blue); border-radius: var(--radius-sm); cursor: pointer;
  transition: var(--fade), box-shadow 0.15s var(--ease), transform 0.12s var(--ease);
}
button:hover, .btn:hover { background: var(--blue-soft); color: var(--blue-deep); }
button:active, .btn:active { transform: translateY(1px) scale(0.98); }
/* primary: solid blue with the fine gold inner ring and a soft blue drop */
.btn-primary, .btn-save.is-dirty { color: var(--on-accent); background: var(--blue-solid);
  border-color: var(--blue-solid);
  box-shadow: var(--gold-ring), 0 6px 14px -8px rgba(31, 58, 147, 0.6); }
.btn-primary:hover, .btn-save.is-dirty:hover { color: var(--on-accent);
  background: var(--blue-solid-hover); border-color: var(--blue-solid-hover);
  box-shadow: inset 0 0 0 1px var(--gold), 0 8px 18px -8px rgba(31, 58, 147, 0.7); }
.btn-confirm { color: var(--on-accent); background: var(--red-solid);
  border-color: var(--red-solid); }
.btn-confirm:hover { color: var(--on-accent); background: var(--red-solid);
  border-color: var(--red-solid); text-decoration: underline; }
.btn-quiet { color: var(--ink); border-color: var(--control); }
.btn-quiet:hover { color: var(--blue-deep); border-color: var(--blue); }
.btn-sm { min-height: var(--control-h-sm); padding: var(--sp-1) var(--sp-3);
  font-size: var(--text-sm); }
.btn-icon { width: var(--control-h-sm); padding: 0; color: var(--muted);
  border-color: var(--control); }
.btn-icon .icon { width: 1rem; height: 1rem; }
/* a row's Save stays quiet until something in that row changed (motion.js adds .is-dirty) */
.btn-save { color: var(--muted); border-color: var(--control); }
button .icon { width: 1.125em; height: 1.125em; flex: none; }
/* In flight: the icon gives way to the spinner mounted by motion.js (no layout change). */
button[aria-busy="true"] { cursor: progress; }
button[aria-busy="true"] > .icon { opacity: 0; }
button[aria-busy="true"] > .lottie-layer {
  inset: auto; left: calc(var(--sp-4) - 0.07em); top: 50%; width: 1.25em; height: 1.25em;
  margin-top: -0.625em;
}
button.btn-sm[aria-busy="true"] > .lottie-layer { left: calc(var(--sp-3) - 0.07em); }
form.inline { display: inline-block; margin: 0; }
form.row-form { display: none; }

/* ---------- forms: grouped under a legend, divided, hints in grey ---------- */
fieldset { border: 0; margin: 0; padding: 0; min-width: 0; }
legend { padding: 0; margin-bottom: var(--sp-4); font-size: var(--text-xl);
  font-weight: var(--w-semibold); line-height: var(--lh-snug); color: var(--blue-deep);
  letter-spacing: var(--track-snug); }
.form-grid { display: grid; gap: var(--sp-4) var(--sp-5); grid-template-columns: minmax(0, 1fr); }
.form-grid .wide { grid-column: 1 / -1; }
.field { display: flex; flex-direction: column; gap: var(--sp-1); min-width: 0; }
.label-row { display: flex; align-items: baseline; gap: var(--sp-2); flex-wrap: wrap; }
label { font-size: var(--text-md); font-weight: var(--w-medium); color: var(--ink); min-width: 0; }
.hint { font-size: var(--text-sm); font-weight: var(--w-regular); color: var(--muted); }
input[type=text], select, textarea {
  width: 100%; min-height: var(--control-h); padding: var(--sp-2) var(--sp-3);
  font: inherit; font-size: var(--text-base); font-weight: var(--w-regular);
  line-height: var(--lh-ui); color: var(--ink);
  background: var(--surface); border: 1px solid var(--control); border-radius: var(--radius-sm);
  transition: var(--fade), box-shadow 0.15s var(--ease);
}
textarea { resize: vertical; min-height: 5.5rem; }
input::placeholder, textarea::placeholder { color: var(--muted); }
input[type=text]:hover, select:hover, textarea:hover { border-color: var(--blue); }
select {
  appearance: none; -webkit-appearance: none; padding-right: var(--sp-6); cursor: pointer;
  background-image: linear-gradient(45deg, transparent 50%, var(--blue) 50%),
    linear-gradient(135deg, var(--blue) 50%, transparent 50%);
  background-position: calc(100% - 1.125rem) 50%, calc(100% - 0.8125rem) 50%;
  background-size: 0.3125rem 0.3125rem, 0.3125rem 0.3125rem; background-repeat: no-repeat;
}
label.check { display: flex; align-items: center; gap: var(--sp-2); font-weight: var(--w-regular);
  cursor: pointer; min-height: var(--control-h); }
input[type=checkbox] {
  appearance: none; -webkit-appearance: none; flex: none; position: relative;
  width: 1.25rem; height: 1.25rem; margin: 0; cursor: pointer;
  background: var(--surface); border: 1px solid var(--control); border-radius: var(--radius-sm);
  transition: var(--fade);
}
input[type=checkbox]:hover { border-color: var(--blue); }
input[type=checkbox]:checked { background: var(--blue-solid); border-color: var(--blue-solid); }
input[type=checkbox]::after {
  content: ""; position: absolute; left: 0.4375rem; top: 0.125rem; width: 0.3125rem;
  height: 0.625rem; border: solid var(--on-accent); border-width: 0 2px 2px 0;
  transform: rotate(45deg) scale(0); transition: transform 0.12s var(--ease);
}
input[type=checkbox]:checked::after { transform: rotate(45deg) scale(1); }
input[type=checkbox]:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px;
  box-shadow: inset 0 0 0 1px var(--gold); }
.help { font-size: var(--text-sm); color: var(--muted); line-height: var(--lh-ui); }
.form-note { margin-top: var(--sp-4); }
.form-actions { display: flex; gap: var(--sp-3); align-items: center; flex-wrap: wrap;
  margin-top: var(--sp-5); padding-top: var(--sp-4); border-top: 1px solid var(--hairline); }
.inline-form { display: flex; gap: var(--sp-3); align-items: flex-end; flex-wrap: wrap; }
.inline-form .field { flex: 1 1 14rem; }
.card > .inline-form, .divided.inline-form { margin-top: var(--sp-4); }

/* ---------- tables: real columns, tight rows ---------- */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th {
  padding: var(--sp-2); text-align: left; white-space: nowrap;
  font-size: var(--text-xs); font-weight: var(--w-semibold); letter-spacing: var(--track-caps);
  text-transform: uppercase; color: var(--muted); background: var(--surface-2);
  border-bottom: 1px solid var(--hairline); transition: var(--fade);
}
td { padding: var(--sp-2); text-align: left; vertical-align: middle;
  min-height: 3rem; border-top: 1px solid var(--hairline);
  transition: border-color 0.2s var(--ease); }
th:first-child, td:first-child { padding-left: var(--sp-4); }
th:last-child, td:last-child { padding-right: var(--sp-4); }
tbody tr:first-child td { border-top: 0; }
tbody tr { transition: background-color 0.15s var(--ease); }
tbody tr:hover { background: var(--row-hover); }
td.num { color: var(--muted); width: 3rem; }
td input[type=text], td select { min-height: var(--control-h-sm); padding: var(--sp-1) var(--sp-2);
  font-size: var(--text-md); }
td select { padding-right: var(--sp-5); }
td.cell-field select { min-width: 7rem; }
td.cell-field input[type=text] { min-width: 8rem; }
.actions { display: flex; gap: var(--sp-2); flex-wrap: nowrap; align-items: center; }
td.actions-cell { white-space: nowrap; }
td.cell-check { width: 4rem; }
td.cell-check input { display: block; }
.role { font-size: var(--text-sm); font-weight: var(--w-medium); color: var(--blue-deep);
  text-transform: lowercase; }

/* ---------- badges: small 2px rectangles with a square marker (status) ---------- */
.badge {
  display: inline-flex; align-items: center; gap: var(--sp-2);
  padding: 0.125rem var(--sp-3); border-radius: var(--radius-sm); border: 1px solid;
  font-size: var(--text-sm); font-weight: var(--w-semibold); letter-spacing: var(--track-badge);
  line-height: var(--lh-ui); white-space: nowrap; transition: var(--fade);
}
.badge::before { content: ""; width: 0.4375rem; height: 0.4375rem; border-radius: 0;
  background: currentColor; }
.badge-on { color: var(--blue-deep); background: var(--blue-soft); border-color: var(--blue-line); }
.badge-off { color: var(--red-deep); background: var(--red-soft); border-color: var(--red-line);
  cursor: help; }
.status { display: inline-flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }

/* ---------- lists ---------- */
.rows { list-style: none; margin: 0; padding: 0; }
.rows > li {
  display: flex; align-items: center; justify-content: space-between; gap: var(--sp-4);
  padding: var(--sp-2) 0; border-top: 1px solid var(--hairline); min-height: 3rem;
}
.rows > li > form { flex: none; }
.rows > li:first-child { border-top: 0; }
.rows .name { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; min-width: 0;
  flex: 1 1 0; }

/* ---------- empty states: say what is missing and offer the next step ---------- */
.empty {
  display: flex; flex-direction: column; align-items: center; text-align: center;
  gap: var(--sp-2); padding: var(--sp-7) var(--sp-5); color: var(--muted);
}
.empty-title { color: var(--blue-deep); font-weight: var(--w-semibold); font-size: var(--text-lg); }
.empty-text { max-width: var(--measure-lead); }
.empty .btn { margin-top: var(--sp-2); }
.empty.compact { padding: var(--sp-5) var(--sp-2); }

/* ---------- facts (model page and home): a line each, the value in a small chip ---------- */
.facts { display: flex; flex-direction: column; gap: var(--sp-3); }
.fact { display: block; }
.fact strong {
  display: inline-block; margin-left: var(--sp-1); padding: 0.125rem var(--sp-3);
  border-radius: var(--radius-sm); border: 1px solid var(--blue-line);
  font-size: var(--text-sm); color: var(--blue-deep); background: var(--blue-soft);
}
.fact-on strong { color: var(--red-deep); background: var(--red-soft);
  border-color: var(--red-line); }
.model-name { font-size: var(--text-xl); font-weight: var(--w-semibold);
  letter-spacing: var(--track-snug); color: var(--blue-deep); overflow-wrap: anywhere; }

/* ---------- root cards ---------- */
.root-card { display: flex; flex-direction: column; gap: var(--sp-4); }
.root-path { font-size: var(--text-base); font-weight: var(--w-semibold); color: var(--blue-deep);
  overflow-wrap: anywhere; }
.root-card .card-head { margin-bottom: 0; }

/* ---------- help block ("Como usar esta página"): CSS-only <details> ---------- */
.help-block {
  max-width: 52rem; border: 1px solid var(--blue-line); border-radius: var(--radius-md);
  background: var(--blue-soft); color: var(--blue-deep); transition: var(--fade);
}
.help-block summary {
  list-style: none; cursor: pointer; display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4); font-size: var(--text-md); font-weight: var(--w-semibold);
  border-radius: var(--radius-md);
}
.help-block summary::-webkit-details-marker { display: none; }
.help-block summary::after {
  content: ""; margin-left: auto; width: 0.4375rem; height: 0.4375rem; flex: none;
  border: solid currentColor; border-width: 0 2px 2px 0; transform: rotate(45deg);
  transition: transform 0.15s var(--ease);
}
.help-block[open] summary::after { transform: rotate(-135deg); }
.help-body { padding: 0 var(--sp-4) var(--sp-4); color: var(--ink);
  line-height: var(--lh-prose); }
.help-body p + p { margin-top: var(--sp-2); }
.help-links { display: flex; flex-wrap: wrap; gap: var(--sp-1) var(--sp-4);
  font-size: var(--text-md);
  font-weight: var(--w-medium); }

/* ---------- manual ---------- */
a.card.manual-link { display: flex; align-items: center; gap: var(--sp-4); flex-wrap: wrap;
  border-color: var(--blue-line); background: var(--blue-soft); }
a.card.manual-link:hover { border-color: var(--blue); }
.manual-link > div { flex: 1 1 16rem; }
.manual-link .cta-title { display: block; font-size: var(--text-lg); font-weight: var(--w-semibold);
  color: var(--blue-deep); }
.manual-link p { color: var(--ink); font-size: var(--text-md); }
.more { display: inline-flex; align-items: center; gap: var(--sp-1); font-size: var(--text-md);
  font-weight: var(--w-semibold); color: var(--blue); }
.more .icon { width: 1.125em; height: 1.125em; }
.manual { display: grid; gap: var(--sp-5); align-items: start;
  grid-template-columns: minmax(0, 1fr); }
.manual-toc-wide { display: none; }
.manual-toc-narrow {
  background: var(--surface); border: 1px solid var(--hairline); border-radius: var(--radius-md);
  box-shadow: var(--shadow-soft); padding: 0; transition: var(--fade);
}
.manual-toc-narrow summary {
  list-style: none; cursor: pointer; display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4); font-weight: var(--w-semibold); color: var(--blue-deep);
}
.manual-toc-narrow summary::-webkit-details-marker { display: none; }
.manual-toc-narrow summary::after {
  content: ""; margin-left: auto; width: 0.4375rem; height: 0.4375rem; flex: none;
  border: solid currentColor; border-width: 0 2px 2px 0; transform: rotate(45deg);
  transition: transform 0.15s var(--ease);
}
.manual-toc-narrow[open] summary::after { transform: rotate(-135deg); }
.manual-toc-narrow .toc-list { padding: 0 var(--sp-2) var(--sp-3); }
.toc-title { font-size: var(--text-xs); font-weight: var(--w-semibold);
  letter-spacing: var(--track-caps); text-transform: uppercase; color: var(--muted);
  padding: 0 var(--sp-3) var(--sp-2); }
.toc-list { list-style: none; margin: 0; padding: 0; counter-reset: toc;
  display: flex; flex-direction: column; gap: var(--sp-1); }
.toc-list a {
  display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-1) var(--sp-3);
  border-radius: var(--radius-sm); font-size: var(--text-md); font-weight: var(--w-medium);
  line-height: var(--lh-snug); color: var(--muted); text-decoration: none; transition: var(--fade);
}
.toc-list a::before {
  counter-increment: toc; content: counter(toc); flex: none; display: grid; place-items: center;
  width: 1.5rem; height: 1.5rem; border-radius: var(--radius-sm); font-size: var(--text-xs);
  font-weight: var(--w-semibold); color: var(--blue-deep); background: var(--blue-soft);
  border: 1px solid var(--blue-line);
}
.toc-list a:hover { color: var(--blue-deep); background: var(--blue-soft); }
/* each chapter is a card; the prose inside keeps the 46rem measure */
.manual-body { display: flex; flex-direction: column; gap: var(--sp-5); min-width: 0;
  max-width: calc(var(--measure-prose) + 2 * var(--sp-5) + 2px); counter-reset: chapter; }
.chapter { counter-increment: chapter; scroll-margin-top: var(--anchor-offset); }
.chapter-head { display: flex; align-items: center; gap: var(--sp-4); margin-bottom: var(--sp-5);
  padding-bottom: var(--sp-4); border-bottom: 1px solid var(--hairline); }
.chapter-head h2 { font-size: var(--text-xl); }
.chapter-num {
  flex: none; display: grid; place-items: center; width: 2.5rem; height: 2.5rem;
  border-radius: var(--radius-sm); color: var(--on-accent); background: var(--blue-solid);
  box-shadow: var(--gold-ring); font-size: var(--text-lg); font-weight: var(--w-semibold);
}
.chapter-num::before { content: counter(chapter); }
.chapter-lead { color: var(--muted); margin-top: var(--sp-1); }
.prose { font-size: var(--text-base); line-height: var(--lh-prose); overflow-wrap: anywhere; }
.prose h3, .chapter h3 {
  display: flex; align-items: center; gap: var(--sp-2); margin: var(--sp-5) 0 var(--sp-2);
  scroll-margin-top: var(--anchor-offset);
}
.prose h3::before { content: ""; flex: none; width: 1.125rem; height: 1px;
  background: var(--gold); }
.prose > h3:first-child { margin-top: 0; }
.prose p + p, .prose ul + p, .prose p + ul, .prose p + ol, .prose p + dl, .prose p + pre,
.prose p + .problems { margin-top: var(--sp-3); }
.prose ul { margin: 0; padding-left: var(--sp-5); display: flex; flex-direction: column;
  gap: var(--sp-2); }
.prose ul ::marker { color: var(--blue); }
.prose ul.plain-list { gap: var(--sp-3); }
.prose li strong:first-child { color: var(--blue-deep); }
.term { color: var(--blue); text-decoration-style: dotted; text-decoration-thickness: 1px;
  text-decoration-color: var(--gold); }
.step-link { margin-left: var(--sp-3); font-size: var(--text-sm); font-weight: var(--w-medium); }
.steps { list-style: none; margin: var(--sp-3) 0 0; padding: 0; counter-reset: step;
  display: flex; flex-direction: column; gap: var(--sp-3); }
.steps > li {
  position: relative; counter-increment: step; padding: var(--sp-4) var(--sp-4) var(--sp-4) 3.5rem;
  background: var(--surface-2); border: 1px solid var(--hairline); border-radius: var(--radius-md);
  transition: var(--fade);
}
.steps > li::before {
  content: counter(step); position: absolute; left: var(--sp-3); top: var(--sp-4);
  display: grid; place-items: center; width: 1.875rem; height: 1.875rem;
  border-radius: var(--radius-sm);
  font-size: var(--text-md); font-weight: var(--w-semibold); color: var(--on-accent);
  background: var(--blue-solid); box-shadow: var(--gold-ring);
}
.step-title { display: block; margin-bottom: var(--sp-1); font-weight: var(--w-semibold);
  color: var(--blue-deep); line-height: var(--lh-snug); }
.example { display: block; margin-top: var(--sp-2); padding: var(--sp-1) var(--sp-3);
  font-size: var(--text-md); color: var(--muted); background: var(--surface);
  border-left: 2px solid var(--gold); border-radius: 0 var(--radius-md) var(--radius-md) 0;
  overflow-wrap: anywhere; }
.example strong { color: var(--blue-deep); }
.callout {
  position: relative; display: flex; gap: var(--sp-3); align-items: flex-start;
  margin: var(--sp-4) 0;
  padding: var(--sp-3) var(--sp-4); border: 1px solid; border-radius: var(--radius-md);
  transition: var(--fade);
}
.callout .icon { margin-top: 0.125rem; }
.callout-title { display: block; margin-bottom: 0.125rem; }
.callout p + p { margin-top: var(--sp-2); }
.callout-tip { background: var(--blue-soft); border-color: var(--blue-line); color: var(--ink); }
.callout-tip .icon, .callout-tip .callout-title { color: var(--blue); }
.callout-warn { background: var(--red-soft); border-color: var(--red-line); color: var(--ink); }
.callout-warn .icon, .callout-warn .callout-title { color: var(--red); }
.callout code { background: var(--surface); }
.tree-figure { margin: 0; }
.tree-figure figcaption { margin: 0 0 var(--sp-2); }
.tree {
  margin: var(--sp-3) 0 0; padding: var(--sp-3) var(--sp-4); overflow-x: auto; white-space: pre;
  color: var(--blue-deep); background: var(--surface-2); border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
}
.roles { margin: var(--sp-3) 0 0; display: grid; gap: var(--sp-3); }
.roles > div { padding: var(--sp-3) var(--sp-4); background: var(--surface-2);
  border: 1px solid var(--hairline); border-radius: var(--radius-md); transition: var(--fade); }
.roles dt { font-weight: var(--w-semibold); color: var(--blue-deep); }
.roles dd { margin: var(--sp-1) 0 0; color: var(--muted); font-size: var(--text-md); }
.problems { display: flex; flex-direction: column; gap: var(--sp-3); margin-top: var(--sp-3); }
.problem {
  position: relative; padding: var(--sp-3) var(--sp-4); background: var(--surface);
  border: 1px solid var(--hairline); border-left: 3px solid var(--red);
  border-radius: var(--radius-md); scroll-margin-top: var(--anchor-offset);
  transition: var(--fade);
}
.problem-aviso { border-left-color: var(--blue-solid); }
.problem-situacao { border-left-color: var(--gold); }
.problem p + p { margin-top: var(--sp-2); }
.problem ul { margin-top: var(--sp-2); }
.problem strong { color: var(--blue-deep); }
.problem-msg { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2) var(--sp-3); }
.problem-tag {
  flex: none; padding: 0.125rem var(--sp-2); border-radius: var(--radius-sm);
  border: 1px solid var(--red-line); color: var(--red-deep); background: var(--red-soft);
}
.problem-aviso .problem-tag { color: var(--blue-deep); background: var(--blue-soft);
  border-color: var(--blue-line); }
.problem-tag-situacao { color: var(--muted); background: var(--surface-2);
  border-color: var(--hairline-strong); }
.problem-tag, .side-label, table.stack-md td[data-label]::before {
  font-size: var(--text-xs); font-weight: var(--w-semibold); letter-spacing: var(--track-caps);
  text-transform: uppercase;
}
.problem-msg code { color: var(--red-deep); background: var(--red-soft); white-space: normal; }
.problem-aviso .problem-msg code { color: var(--blue-deep); background: var(--blue-soft); }
.glossary { margin: 0; display: grid; gap: var(--sp-3); grid-template-columns: minmax(0, 1fr); }
.term-card { padding: var(--sp-3) var(--sp-4); background: var(--surface-2);
  border: 1px solid var(--hairline); border-radius: var(--radius-md);
  scroll-margin-top: var(--anchor-offset); transition: var(--fade); }
.term-card dt { font-weight: var(--w-semibold); color: var(--blue-deep); }
.term-card dd { margin: var(--sp-1) 0 0; color: var(--muted); line-height: var(--lh-prose); }
.back-top-row { margin-top: var(--sp-5); }
.back-top { display: inline-flex; align-items: center; gap: var(--sp-1); font-size: var(--text-md);
  font-weight: var(--w-medium); text-decoration: none; color: var(--muted); }
.back-top:hover { color: var(--blue); }

/* ---------- Lottie layers (mounted by motion.js; the static icon is the fallback) ---------- */
.lottie-layer { position: absolute; inset: 0; display: block; pointer-events: none; }
.lottie-layer svg { display: block; width: 100%; height: 100%; }
/* Animation files carry fallback colours; these classes repaint them with the theme tokens,
   so light, dark and the thin gold detail follow the palette (CSS beats presentation
   attributes). */
.lt-stroke path { stroke: currentColor; }
.lt-fill path { fill: currentColor; }
.lt-gold-fill path { fill: var(--gold); }

/* ---------- motion: short, once, never looping; all of it off under reduced motion ---------- */
/* Damped-spring pop in four stops: rise with overshoot, dip, small rebound, settle. */
@keyframes pop {
  0% { transform: none; animation-timing-function: cubic-bezier(0.34, 1.4, 0.64, 1); }
  34% { transform: translateY(-2px) scale(1.1) rotate(-3deg);
    animation-timing-function: ease-in-out; }
  62% { transform: translateY(0.5px) scale(0.98) rotate(1deg);
    animation-timing-function: ease-in-out; }
  82% { transform: scale(1.02) rotate(-0.5deg); animation-timing-function: ease-out; }
  100% { transform: none; }
}
@keyframes pop-tap {
  0% { transform: none; animation-timing-function: ease-out; }
  34% { transform: scale(0.92); animation-timing-function: ease-in-out; }
  68% { transform: scale(1.04); animation-timing-function: ease-out; }
  100% { transform: none; }
}
/* Menu icons: only a pop (scale up and settle, no rotation, no glow) on hover/focus, when
   pressed and, through the .wake class that /motion.js adds after first paint, on the current
   section when a page opens. Each starts from the icon as painted (no delay, no backwards
   fill), so nothing dips before it pops. Two keyframes with the same shape so that moving
   from one state to the next restarts the pop. */
@keyframes wake {
  0% { transform: none; }
  45% { transform: scale(1.12); }
  100% { transform: none; }
}
@keyframes nav-pop {
  0% { transform: none; }
  45% { transform: scale(1.12); }
  100% { transform: none; }
}
.nav a.wake .icon { animation: wake 0.52s var(--ease); }
.nav a:hover .icon, .nav a:focus-visible .icon { animation: nav-pop 0.45s var(--ease); }
.nav a:active .icon { animation: wake 0.4s var(--ease); }
/* Page entrance: each block pops in (fade, small lift, a hair of overshoot), staggered by
   position. Fill mode "backwards" only covers the short delay: when the animation ends (or if
   it never runs) the normal styles apply, so nothing is left hidden. Only opacity and transform
   move, so the layout never shifts. Longhands, so the delays below are not reset. */
@keyframes rise {
  0% { opacity: 0; transform: translateY(8px) scale(0.97); }
  60% { opacity: 1; transform: translateY(-1px) scale(1.006); }
  100% { opacity: 1; transform: none; }
}
main > :not(.split):not(.manual), .split > :not(.stack), .stack > *,
.manual > :not(.manual-body), .manual-body > * {
  animation-name: rise; animation-duration: 0.42s; animation-timing-function: var(--ease);
  animation-fill-mode: backwards;
}
main > :nth-child(2), .split > :nth-child(1), .stack > :nth-child(1), .manual > *,
.manual-body > :nth-child(1) { animation-delay: 0.05s; }
main > :nth-child(3), .split > :nth-child(2), .stack > :nth-child(2),
.manual-body > :nth-child(2) { animation-delay: 0.1s; }
main > :nth-child(4), .stack > :nth-child(3), .manual-body > :nth-child(3) {
  animation-delay: 0.15s; }
main > :nth-child(n + 5), .stack > :nth-child(n + 4), .manual-body > :nth-child(n + 4) {
  animation-delay: 0.2s; }

/* ---------- responsive ---------- */
@media (max-width: 519px) {
  .nav { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--sp-1); }
  .nav a { flex-direction: column; justify-content: center; text-align: center;
    padding: var(--sp-2) var(--sp-1); gap: var(--sp-1); font-size: var(--text-xs); }
  .nav a::after { left: var(--sp-4); right: var(--sp-4); }
  .chapter-head { align-items: flex-start; }
  .chapter-num { width: 2.25rem; height: 2.25rem; font-size: var(--text-base); }
  .steps > li { padding-left: 3rem; }
  .steps > li::before { left: var(--sp-2); }
}
@media (min-width: 520px) and (max-width: 899px) {
  :root { --anchor-offset: 7rem; } /* the compact top bar is sticky here */
  .sidebar { position: sticky; top: 0; }
}
/*STACK-MD*/
/* between the phone and the wide table (sidebar present): a row is a small grid, not a column */
@media (min-width: 761px) and (max-width: 1199px) {
  table.stack-md tr { display: grid; grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr));
    gap: var(--sp-1) var(--sp-4); align-items: start; padding: var(--sp-3) var(--sp-4); }
  table.stack-md td, table.stack-md td:first-child, table.stack-md td:last-child {
    flex-direction: column; align-items: flex-start; justify-content: flex-start;
    text-align: left; gap: var(--sp-1); padding: var(--sp-1) 0; }
  table.stack-md td.cell-field { align-items: stretch; }
  table.stack-md td.actions-cell { grid-column: 1 / -1; flex-direction: row; align-items: center; }
  table.stack-md td.actions-cell::before { display: none; }
}
@media (max-width: 760px) {
  .brand-sub { display: none; }
}
@media (min-width: 620px) {
  .form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (min-width: 640px) {
  main { padding: var(--sp-6) var(--sp-5) var(--sp-7); gap: var(--sp-5); }
  .footer { padding: var(--sp-4) var(--sp-5) var(--sp-5); }
  .card { padding: var(--sp-5); }
  .card::before { left: var(--sp-5); right: var(--sp-5); }
  .card.flush { padding: 0; }
  .notice { padding: var(--sp-5); }
}
@media (min-width: 900px) {
  .shell { display: grid; grid-template-columns: 16rem minmax(0, 1fr); align-items: start; }
  .sidebar {
    position: sticky; top: 0; height: 100vh; overflow-y: auto;
    display: flex; flex-direction: column; align-items: stretch; gap: var(--sp-5);
    padding: var(--sp-5) var(--sp-4) var(--sp-4);
    border-bottom: 0; border-right: 1px solid var(--hairline);
    box-shadow: 8px 0 40px -28px rgba(18, 23, 43, 0.3);
  }
  .sidebar::after {
    left: auto; right: -1px; top: 0; bottom: 0; width: 1px; height: auto;
    background: linear-gradient(180deg, transparent, var(--gold) 15%, var(--gold) 55%, transparent);
  }
  .brand { padding: 0 var(--sp-2); }
  .brand svg { width: 2.5rem; height: 2.5rem; }
  .nav { flex-direction: column; flex-wrap: nowrap; gap: var(--sp-1); }
  .nav a { padding: var(--sp-2) var(--sp-3); }
  .nav a::before {
    content: ""; position: absolute; left: 0; top: 22%; bottom: 22%; width: 2px;
    border-radius: 2px; background: var(--gold); opacity: 0; transform: scaleY(0.4);
    transition: opacity 0.25s var(--ease), transform 0.25s var(--ease);
  }
  .nav a::after { display: none; }
  .nav a.nav-help { margin-top: var(--sp-4); }
  .nav a:hover { transform: translateX(3px); }
  .nav a:hover::before { opacity: 0.6; transform: none; }
  .nav a[aria-current="page"]::before { opacity: 1; transform: none; }
  .side-foot { margin-top: auto; flex-direction: column; align-items: stretch;
    gap: var(--sp-3); padding: var(--sp-4) var(--sp-2) 0;
    border-top: 1px solid var(--hairline); transition: border-color 0.35s var(--ease); }
  .side-switch { display: flex; align-items: center; justify-content: space-between; }
  .side-label { display: block; }
  .access-badge { display: flex; }
  main { padding: var(--sp-6) var(--sp-6) var(--sp-7); }
  .footer { display: none; }
  td code { overflow-wrap: normal; white-space: nowrap; }
}
@media (min-width: 760px) {
  .glossary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .roles { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (min-width: 1200px) {
  .manual { grid-template-columns: 15.5rem minmax(0, 1fr); gap: var(--sp-6); }
  .manual-toc-narrow { display: none; }
  .manual-toc-wide {
    display: block; position: sticky; top: var(--sp-5); max-height: calc(100vh - 3rem);
    overflow-y: auto; padding: var(--sp-4) var(--sp-2); background: var(--surface);
    border: 1px solid var(--hairline); border-radius: var(--radius-lg);
    box-shadow: var(--shadow-soft); transition: var(--fade);
  }
}
@media (min-width: 1100px) {
  .form-grid.fields-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .form-grid.fields-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
}
@media (min-width: 1360px) {
  .split { grid-template-columns: minmax(0, 1fr) minmax(22rem, 26rem); }
  .split > :only-child { grid-column: 1 / -1; }
  .split > .aside .form-grid { grid-template-columns: minmax(0, 1fr); }
  .split.split-rev { grid-template-columns: minmax(22rem, 26rem) minmax(0, 1fr); }
}
@media (min-width: 1360px) and (max-width: 1479px) {
  .split.split-table { grid-template-columns: minmax(0, 1fr); }
  .split.split-table > .aside .form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (min-width: 1500px) {
  /* thin gold hairlines mark the edges of the content column when the margins grow */
  main::before, main::after {
    content: ""; position: absolute; top: var(--sp-6); bottom: var(--sp-7); width: 1px;
    pointer-events: none; opacity: 0.4;
    background: linear-gradient(180deg, transparent, var(--gold) 18%, transparent 70%);
  }
  main::before { left: 0; }
  main::after { right: 0; }
}

/* ---------- print: black on white, no navigation, whole manual readable ---------- */
@media print {
  :root:root:root {
    --paper: #fff; --surface: #fff; --surface-2: #fff; --row-hover: #fff; --ink: #000;
    --muted: #222; --hairline: #999; --hairline-strong: #666; --control: #444;
    --blue: #000; --blue-solid: #000; --blue-deep: #000; --blue-soft: #fff; --blue-line: #666;
    --red: #000; --red-solid: #000; --red-deep: #000; --red-soft: #fff; --red-line: #666;
    --gold: #666; --on-accent: #fff; --shadow-soft: none; --shadow-lift: none; --gold-ring: none;
    color-scheme: light;
  }
  html, body { background: #fff !important; color: #000 !important; }
  .page-head::after, .card::before, .notice::before, .alert-blocked::before,
  .alert-success::before, .confirm::before, .callout-warn::before, .sidebar::after,
  main::before, main::after, .prose h3::before { display: none !important; }
  .sidebar, .skip, .manual-toc-narrow, .back-top-row, .help-block, .footer, .theme-switch,
  .lottie-layer, body::before, body::after {
    display: none !important;
  }
  .manual-toc-wide {
    display: block !important; position: static; max-height: none; overflow: visible;
    border: 1px solid #999; margin-bottom: var(--sp-4); break-after: page;
  }
  .shell, .manual { display: block; }
  main { max-width: none; padding: 0; gap: var(--sp-3); }
  .manual-body { max-width: none; gap: var(--sp-4); }
  .card, .callout, .problem, .steps > li, .term-card, .tree, .example, .roles > div {
    box-shadow: none !important; background: #fff !important; border-color: #999 !important;
    color: #000 !important; break-inside: avoid;
  }
  .card { padding: var(--sp-2) 0; border: 0 !important; }
  .chapter-num { background: #fff !important; color: #000 !important; border: 1px solid #000; }
  h1, h2, h3 { color: #000 !important; break-after: avoid; }
  a { color: #000; text-decoration: underline; }
  code { color: #000 !important; background: #fff !important; border: 1px solid #999; }
  .steps > li::before { background: #fff !important; color: #000 !important;
    border: 1px solid #000; }
  .chapter { break-before: page; }
  #comece { break-before: auto; }
  .tree { overflow: visible; white-space: pre-wrap; }
  * { animation: none !important; transition: none !important; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation: none !important;
    transition: none !important;
    scroll-behavior: auto !important;
  }
  .card.lift:hover, a.card:hover, a.card:hover .more, button:active, .btn:active,
  .nav a:hover { transform: none; }
}
"""

# A table whose cells carry data-label turns into one block per row below 1200px (the sidebar
# takes 16rem of the width above 900px, and the editable rows need about 860px): label above
# the value, form controls full width.
_STACKED: Final = """\
  TABLE, TABLE tbody { display: block; }
  TABLE thead { position: absolute; width: 1px; height: 1px; overflow: hidden;
    clip-path: inset(50%); }
  TABLE tr { display: block; padding: var(--sp-3) var(--sp-4); border-top: 1px solid
    var(--hairline); }
  TABLE tbody tr:first-child { border-top: 0; }
  TABLE td, TABLE td:first-child, TABLE td:last-child { display: flex;
    justify-content: space-between; align-items: center; gap: var(--sp-4);
    padding: var(--sp-1) 0; border: 0; text-align: right; }
  TABLE td[data-label]::before { content: attr(data-label); flex: none; text-align: left;
    color: var(--muted); }
  TABLE td.cell-field { flex-direction: column; align-items: stretch; text-align: left;
    gap: var(--sp-1); }
  TABLE td.cell-field input[type=text], TABLE td.cell-field select { min-height: var(--control-h);
    font-size: var(--text-base); }
  TABLE td.num { width: auto; }
  TABLE td.cell-check { width: auto; }
  TABLE td.actions-cell { justify-content: flex-start; white-space: normal; }
  .actions { flex-wrap: wrap; }
"""

# The one typography switch. "serif" (the default): IBM Plex Serif on page titles, card titles,
# legends and the home counts, IBM Plex Sans everywhere else. "sans": IBM Plex Sans everywhere.
# Either way IBM Plex Mono sets folders and codes, and the serif never reaches small UI text or
# a form control. The browser only downloads a face a page actually uses.
DISPLAY_VARIANTS: Final = {"serif": "var(--serif)", "sans": "var(--font)"}
DISPLAY: Final = "serif"


def build(display: str = DISPLAY) -> str:
    """The whole stylesheet for one display variant (see ``DISPLAY_VARIANTS``)."""
    return _FACES + _CSS.replace("/*DARK*/", _DARK_TOKENS).replace(
        "/*DISPLAY*/", DISPLAY_VARIANTS[display]
    ).replace(
        "/*STACK-MD*/",
        "@media (max-width: 1199px) {\n" + _STACKED.replace("TABLE", "table.stack-md") + "}",
    )


def preload_fonts(display: str = DISPLAY) -> tuple[str, ...]:
    """Fonts every page needs at once (``<link rel="preload">``): the text face, plus the serif
    when it sets the titles."""
    return ("sans-400-latin1", "serif-600-latin1") if display == "serif" else ("sans-400-latin1",)


CSS: Final = build()
PRELOAD_FONTS: Final = preload_fonts()
