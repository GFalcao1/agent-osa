"""Stylesheet of the local panel, served by ``/panel.css``.

One file on purpose: the CSP is ``default-src 'self'; style-src 'self'``, so there is no
inline ``<style>``, no ``style=""`` attribute, no web font and no external reference (the
security tests also refuse any ``http://``, ``https://`` or ``@import`` in this text).
Palette: white and pale-blue surfaces (a deep navy ink in the dark theme), blue as the
primary color, red for destructive actions, errors and "inativo", and gold only as thin
details (1px rules, active-nav marker, focus inner line, hairlines at the page edges).
Both themes are CSS custom properties: ``:root`` is light; the same dark values apply under
``[data-theme="dark"]`` and, before any script runs or without one, under
``prefers-color-scheme: dark`` unless the page is pinned to light. Layout is a fixed left
sidebar from 900px up and a compact top bar below it. Motion is CSS only and switched off
under ``prefers-reduced-motion``.
"""

from typing import Final

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
  --layer-light: 0;
  --layer-dark: 1;
  --shadow-soft: 0 1px 2px rgba(0, 0, 0, 0.35), 0 14px 34px -14px rgba(0, 0, 0, 0.65),
    0 36px 70px -36px rgba(30, 60, 160, 0.3);
  --shadow-lift: 0 2px 4px rgba(0, 0, 0, 0.4), 0 22px 44px -16px rgba(0, 0, 0, 0.75),
    0 40px 80px -36px rgba(60, 100, 220, 0.4);
  color-scheme: dark;
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
  --layer-light: 1;
  --layer-dark: 0;
  --anchor-offset: 1rem;
  --radius-lg: 20px;
  --radius-md: 12px;
  --shadow-soft: 0 1px 2px rgba(18, 23, 43, 0.05), 0 12px 32px -14px rgba(18, 23, 43, 0.16),
    0 32px 64px -34px rgba(31, 58, 147, 0.18);
  --shadow-lift: 0 2px 4px rgba(18, 23, 43, 0.06), 0 22px 44px -16px rgba(31, 58, 147, 0.3),
    0 40px 80px -36px rgba(31, 58, 147, 0.26);
  --font: ui-sans-serif, -apple-system, "SF Pro Text", "Segoe UI", Roboto, Helvetica, Arial,
    sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --ease: cubic-bezier(0.2, 0.7, 0.2, 1);
  --fade: background-color 0.35s var(--ease), color 0.35s var(--ease),
    border-color 0.35s var(--ease);
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
  font-family: var(--font);
  font-size: 1rem;
  line-height: 1.55;
  color: var(--ink);
  -webkit-font-smoothing: antialiased;
  transition: color 0.35s var(--ease);
}
/* Two fixed backdrop layers that cross-fade with the theme: a very soft gradient plus a
   hairline lattice. Cards are opaque, so the lattice only shows in the margins. */
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
  letter-spacing: -0.02em;
  line-height: 1.2;
  text-wrap: balance;
  transition: color 0.35s var(--ease);
}
h1 { font-size: clamp(1.6rem, 1.25rem + 1.6vw, 2.15rem); font-weight: 650; }
h2 { font-size: 1.2rem; font-weight: 620; }
p { margin: 0; }
a { color: var(--blue); text-decoration-color: var(--link-line); text-underline-offset: 3px;
  transition: color 0.15s var(--ease), text-decoration-color 0.15s var(--ease); }
a:hover { color: var(--blue-deep); text-decoration-color: var(--gold); }
code {
  font-family: var(--mono);
  font-size: 0.86em;
  padding: 0.1em 0.45em;
  color: var(--blue-deep);
  background: var(--blue-soft);
  border-radius: 6px;
  overflow-wrap: anywhere;
  transition: var(--fade);
}
strong { font-weight: 620; }
.muted { color: var(--muted); }
.skip {
  position: absolute; left: 1rem; top: -4rem; z-index: 40;
  padding: 0.5rem 1rem; border-radius: 999px;
  background: var(--blue-solid); color: var(--on-accent); text-decoration: none;
  transition: top 0.15s var(--ease);
}
.skip:focus { top: 0.75rem; }
.icon { width: 1.25rem; height: 1.25rem; flex: none; }

/* ---------- focus ---------- */
:focus { outline: none; }
:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
  border-radius: 6px;
}
input:focus-visible, select:focus-visible, textarea:focus-visible {
  outline: none;
  border-color: var(--focus);
  box-shadow: inset 0 0 0 1px var(--gold), 0 0 0 3px var(--blue-soft), 0 0 0 4px var(--focus);
}
.btn:focus-visible, button:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 3px;
  border-radius: 999px;
}

/* ---------- shell: sidebar on wide screens, compact top bar below ---------- */
.shell { display: block; }
.sidebar {
  position: relative; z-index: 10;
  display: grid; grid-template-columns: 1fr auto; align-items: center;
  grid-template-areas: "brand foot" "nav nav"; gap: 0.5rem 1rem;
  padding: 0.7rem 1rem;
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
  display: inline-flex; align-items: center; gap: 0.7rem;
  color: var(--blue-deep); text-decoration: none;
}
.brand:hover { color: var(--blue-deep); }
.brand svg { width: 2rem; height: 2rem; flex: none; transition: transform 0.4s var(--ease); }
.brand:hover svg { transform: rotate(-4deg) scale(1.06); }
.brand .shield-fill { fill: var(--blue-solid); transition: fill 0.35s var(--ease); }
.brand .shield-line { stroke: var(--gold); }
.brand .shield-cross { stroke: var(--on-accent); }
.brand .shield-drop { fill: var(--red-solid); }
.brand-text { display: flex; flex-direction: column; line-height: 1.15; }
.brand-name { font-weight: 650; font-size: 1.05rem; letter-spacing: -0.01em; }
.brand-sub { font-size: 0.74rem; font-weight: 520; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted); }
.nav { grid-area: nav; display: flex; flex-wrap: wrap; gap: 0.2rem; }
.nav a {
  position: relative; display: flex; align-items: center; gap: 0.65rem;
  padding: 0.5rem 0.8rem; border-radius: var(--radius-md);
  font-size: 0.94rem; font-weight: 540; color: var(--muted); text-decoration: none;
  transition: var(--fade), transform 0.2s var(--ease);
}
.nav a .icon { width: 1.15rem; height: 1.15rem; transition: color 0.2s var(--ease); }
.nav a::after {
  content: ""; position: absolute; left: 0.8rem; right: 0.8rem; bottom: 0.2rem; height: 1px;
  background: var(--gold); transform: scaleX(0); transform-origin: left;
  transition: transform 0.3s var(--ease);
}
.nav a:hover { color: var(--blue-deep); background: var(--blue-soft); }
.nav a:hover::after { transform: scaleX(1); }
.nav a:hover .icon { color: var(--blue); }
.nav a[aria-current="page"] { color: var(--blue-deep); background: var(--blue-soft); }
.nav a[aria-current="page"] .icon { color: var(--blue); }
.nav a[aria-current="page"]::after { transform: scaleX(1); }
.side-foot { grid-area: foot; display: flex; align-items: center; gap: 0.75rem; }
.side-label { display: none; font-size: 0.78rem; font-weight: 560; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted); }

/* ---------- theme switch: sun and moon on a track, the knob sits on the active one ---------- */
.theme-switch { display: none; }
html.js .theme-switch {
  position: relative; display: inline-block; flex: none;
  width: 3.9rem; height: 2.15rem; min-height: 0; padding: 0;
  background: var(--blue-soft); border: 1px solid var(--control); border-radius: 999px;
  transition: var(--fade), box-shadow 0.2s var(--ease);
}
html.js .theme-switch:hover { background: var(--blue-soft); border-color: var(--blue); }
html.js .theme-switch:active { transform: none; }
.theme-switch .knob {
  position: absolute; top: 0.2rem; left: 0.2rem; width: 1.6rem; height: 1.6rem;
  border-radius: 50%; background: var(--knob); border: 1px solid var(--gold);
  box-shadow: 0 2px 6px -1px rgba(0, 0, 0, 0.35);
  transform: translateX(var(--knob-x));
  transition: transform 0.35s var(--ease), background-color 0.35s var(--ease);
}
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
  padding: 1.5rem 1rem 2.5rem;
  display: flex; flex-direction: column; gap: 1.25rem;
}
.footer {
  max-width: 84rem; width: 100%; margin: 0 auto; padding: 1.5rem 1rem 2rem;
  font-size: 0.85rem; color: var(--muted);
  display: flex; align-items: center; gap: 0.75rem;
}
.footer::before { content: ""; width: 1.5rem; height: 1px; background: var(--gold); }
.page-head { display: flex; flex-direction: column; gap: 0.6rem; max-width: 52rem; }
.page-head .title-row { display: flex; align-items: center; gap: 0.9rem; flex-wrap: wrap; }
.page-head .ornament {
  display: inline-flex; align-items: center; gap: 0;
  width: 2.2rem; height: 1px; background: var(--gold); position: relative; flex: none;
}
.page-head .ornament::after {
  content: ""; position: absolute; right: -2px; top: -3px; width: 7px; height: 7px;
  border: 1px solid var(--gold); transform: rotate(45deg); background: var(--surface);
}
.eyebrow {
  font-size: 0.78rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--muted);
}
.lead { color: var(--muted); font-size: 1.02rem; max-width: 46rem; }
.back {
  display: inline-flex; align-items: center; gap: 0.35rem; align-self: flex-start;
  font-size: 0.92rem; font-weight: 520; text-decoration: none; color: var(--muted);
}
.back:hover { color: var(--blue); }
.grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); }
.split { display: grid; gap: 1.25rem; align-items: start; grid-template-columns: minmax(0, 1fr); }
.stack { display: flex; flex-direction: column; gap: 1.25rem; }

/* ---------- cards ---------- */
.card {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-soft);
  padding: 1.15rem;
  transition: var(--fade), transform 0.25s var(--ease), box-shadow 0.25s var(--ease);
}
.card::before {
  content: ""; position: absolute; left: 1.15rem; right: 1.15rem; top: -1px; height: 1px;
  background: linear-gradient(90deg, transparent, var(--gold), transparent); opacity: 0.6;
  transition: opacity 0.3s var(--ease), left 0.3s var(--ease), right 0.3s var(--ease);
}
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 1rem;
  flex-wrap: wrap; margin-bottom: 1rem; }
.card-head h2 { margin: 0; }
.card.flush { padding: 0; overflow: hidden; }
.card.flush::before { left: 0; right: 0; }
.card.lift:hover, a.card:hover {
  transform: translateY(-3px); box-shadow: var(--shadow-lift); border-color: var(--hairline-strong);
}
.card.lift:hover::before, a.card:hover::before { opacity: 1; left: 0.5rem; right: 0.5rem; }
a.card { text-decoration: none; color: inherit; display: block; }
a.card.stat, .stat { display: flex; flex-direction: column; gap: 0.35rem; }
.stat .label { font-size: 0.85rem; color: var(--muted); font-weight: 520; }
.stat .value { font-size: 2.4rem; font-weight: 650; letter-spacing: -0.03em;
  color: var(--blue-deep); line-height: 1; }
.stat .more { font-size: 0.85rem; color: var(--blue); margin-top: 0.4rem;
  transition: transform 0.25s var(--ease); }
a.card:hover .more { transform: translateX(4px); }

/* ---------- alerts ---------- */
.alert {
  display: flex; gap: 0.85rem; align-items: flex-start;
  padding: 1rem 1.25rem; border-radius: var(--radius-md);
  border: 1px solid; background: var(--surface); transition: var(--fade);
}
.alert .icon { margin-top: 0.15rem; }
.alert p + p { margin-top: 0.25rem; }
.alert-title { display: block; margin-bottom: 0.1rem; }
.alert-error { background: var(--red-soft); border-color: var(--red-line); color: var(--red-deep); }
.alert-error .icon { color: var(--red); }
.alert-info { background: var(--blue-soft); border-color: var(--blue-line);
  color: var(--blue-deep); }
.alert-info .icon { color: var(--blue); }
.alert-blocked { background: var(--surface); border-color: var(--red-line); color: var(--ink);
  box-shadow: var(--shadow-soft); position: relative; }
.alert-blocked::before { content: ""; position: absolute; left: 1rem; right: 1rem; top: -1px;
  height: 1px; background: linear-gradient(90deg, transparent, var(--gold), transparent); }
.alert-blocked .icon { color: var(--red); }
.alert-blocked .alert-title { color: var(--red-deep); }
.alert-blocked code { background: var(--red-soft); color: var(--red-deep); }
.notice {
  position: relative; display: flex; gap: 1rem; align-items: flex-start;
  padding: 1.15rem; border-radius: var(--radius-lg);
  background: var(--red-soft); border: 1px solid var(--red-line); color: var(--red-deep);
  box-shadow: var(--shadow-soft); transition: var(--fade);
}
.notice::before {
  content: ""; position: absolute; left: 1.5rem; right: 1.5rem; top: -1px; height: 1px;
  background: linear-gradient(90deg, transparent, var(--gold), transparent);
}
.notice .icon { width: 1.6rem; height: 1.6rem; color: var(--red); margin-top: 0.1rem; }
.notice .alert-title { font-size: 1.05rem; color: var(--red-deep); margin-bottom: 0.2rem; }
.notice code { background: var(--surface); color: var(--red-deep); }

/* ---------- buttons ---------- */
button, .btn {
  appearance: none; -webkit-appearance: none;
  display: inline-flex; align-items: center; justify-content: center; gap: 0.4rem;
  min-height: 2.5rem; padding: 0.5rem 1.15rem;
  font: inherit; font-size: 0.94rem; font-weight: 580; line-height: 1.2;
  color: var(--blue); background: var(--surface);
  border: 1px solid var(--blue); border-radius: 999px; cursor: pointer;
  transition: var(--fade), box-shadow 0.15s var(--ease), transform 0.12s var(--ease);
}
button:hover { background: var(--blue-soft); color: var(--blue-deep); }
button:active { transform: translateY(1px) scale(0.98); }
.btn-primary { color: var(--on-accent); background: var(--blue-solid);
  border-color: var(--blue-solid);
  box-shadow: inset 0 0 0 1px rgba(214, 178, 94, 0.55), 0 6px 14px -8px rgba(31, 58, 147, 0.6); }
.btn-primary:hover { color: var(--on-accent); background: var(--blue-solid-hover);
  border-color: var(--blue-solid-hover); box-shadow: inset 0 0 0 1px var(--gold),
    0 8px 18px -8px rgba(31, 58, 147, 0.7); }
.btn-danger { color: var(--red); background: var(--surface); border-color: var(--red); }
.btn-danger:hover { color: var(--on-accent); background: var(--red-solid);
  border-color: var(--red-solid); }
.btn-danger:focus-visible { outline-color: var(--red); }
.btn-sm { min-height: 2rem; padding: 0.25rem 0.85rem; font-size: 0.85rem; }
form.inline { display: inline-block; margin: 0; }

/* ---------- forms ---------- */
fieldset { border: 0; margin: 0; padding: 0; min-width: 0; }
legend { padding: 0; margin-bottom: 1rem; font-size: 1.2rem; font-weight: 620;
  color: var(--blue-deep); letter-spacing: -0.02em; }
.form-grid { display: grid; gap: 1rem 1.25rem; grid-template-columns: minmax(0, 1fr); }
.form-grid .wide { grid-column: 1 / -1; }
label { display: flex; flex-direction: column; gap: 0.35rem; font-size: 0.9rem; font-weight: 560;
  color: var(--ink); min-width: 0; }
label .hint { font-weight: 400; color: var(--muted); }
input[type=text], select, textarea {
  width: 100%; min-height: 2.75rem; padding: 0.6rem 0.9rem;
  font: inherit; font-size: 1rem; font-weight: 400; color: var(--ink);
  background: var(--surface); border: 1px solid var(--control); border-radius: var(--radius-md);
  transition: var(--fade), box-shadow 0.2s var(--ease);
}
textarea { resize: vertical; min-height: 5.5rem; line-height: 1.5; }
input::placeholder, textarea::placeholder { color: var(--muted); }
input[type=text]:hover, select:hover, textarea:hover { border-color: var(--blue); }
select {
  appearance: none; -webkit-appearance: none; padding-right: 2.4rem; cursor: pointer;
  background-image: linear-gradient(45deg, transparent 50%, var(--blue) 50%),
    linear-gradient(135deg, var(--blue) 50%, transparent 50%);
  background-position: calc(100% - 1.15rem) 50%, calc(100% - 0.8rem) 50%;
  background-size: 0.35rem 0.35rem, 0.35rem 0.35rem; background-repeat: no-repeat;
}
label.check { flex-direction: row; align-items: center; gap: 0.6rem; font-weight: 500;
  cursor: pointer; min-height: 2.75rem; }
input[type=checkbox] {
  appearance: none; -webkit-appearance: none; flex: none; position: relative;
  width: 1.3rem; height: 1.3rem; margin: 0; cursor: pointer;
  background: var(--surface); border: 1px solid var(--control); border-radius: 6px;
  transition: var(--fade), box-shadow 0.2s var(--ease);
}
input[type=checkbox]:hover { border-color: var(--blue); }
input[type=checkbox]:checked { background: var(--blue-solid); border-color: var(--blue-solid); }
input[type=checkbox]::after {
  content: ""; position: absolute; left: 0.42rem; top: 0.17rem; width: 0.3rem; height: 0.6rem;
  border: solid var(--on-accent); border-width: 0 2px 2px 0; transform: rotate(45deg) scale(0);
  transition: transform 0.15s var(--ease);
}
input[type=checkbox]:checked::after { transform: rotate(45deg) scale(1); }
input[type=checkbox]:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px;
  box-shadow: inset 0 0 0 1px var(--gold); border-radius: 6px; }
.help { font-size: 0.88rem; color: var(--muted); }
.form-actions { display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap;
  margin-top: 1.5rem; }
.inline-form { display: flex; gap: 0.75rem; align-items: flex-end; flex-wrap: wrap; }
.inline-form label { flex: 1 1 14rem; }
.card > .inline-form { margin-top: 1.25rem; }
.card > .rows + .inline-form { padding-top: 1.25rem; border-top: 1px solid var(--hairline); }

/* ---------- tables ---------- */
table { width: 100%; border-collapse: collapse; }
th {
  padding: 0.85rem 1.1rem; text-align: left; white-space: nowrap;
  font-size: 0.75rem; font-weight: 620; letter-spacing: 0.07em; text-transform: uppercase;
  color: var(--muted); background: var(--surface-2); border-bottom: 1px solid var(--hairline);
  transition: var(--fade);
}
td { padding: 0.95rem 1.1rem; text-align: left; vertical-align: middle;
  border-top: 1px solid var(--hairline); transition: border-color 0.35s var(--ease); }
tbody tr:first-child td { border-top: 0; }
tbody tr { transition: background-color 0.15s var(--ease); }
tbody tr:hover { background: var(--row-hover); }
td.num { color: var(--muted); font-variant-numeric: tabular-nums; width: 3rem; }
td.empty-cell { padding: 0; }
.actions { display: flex; gap: 0.4rem; flex-wrap: nowrap; align-items: center; }
td.actions-cell { white-space: nowrap; }
td.cell-form > form { display: flex; flex-wrap: nowrap; gap: 0.75rem 1rem; align-items: flex-end; }
td.cell-form label:not(.check) { flex: 1 1 8rem; }
td.cell-form label.check { white-space: nowrap; flex: none; min-width: max-content; }
td.cell-form button { margin-bottom: 0.1rem; }
td.cell-form label.check { min-height: 2.75rem; }
.role { font-size: 0.85rem; font-weight: 560; color: var(--blue-deep); text-transform: lowercase; }

/* ---------- badges ---------- */
.badge {
  display: inline-flex; align-items: center; gap: 0.4rem;
  padding: 0.2rem 0.7rem; border-radius: 999px; border: 1px solid;
  font-size: 0.8rem; font-weight: 600; line-height: 1.4; white-space: nowrap;
  transition: var(--fade);
}
.badge::before { content: ""; width: 0.45rem; height: 0.45rem; border-radius: 50%;
  background: currentColor; }
.badge-on { color: var(--blue-deep); background: var(--blue-soft); border-color: var(--blue-line); }
.badge-off { color: var(--red-deep); background: var(--red-soft); border-color: var(--red-line); }
.status { display: inline-flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }

/* ---------- lists ---------- */
.rows { list-style: none; margin: 0; padding: 0; }
.rows > li {
  display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap;
  padding: 0.8rem 0; border-top: 1px solid var(--hairline);
}
.rows > li:first-child { border-top: 0; }
.rows .name { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; min-width: 0; }
.rows li.empty-row { color: var(--muted); }

/* ---------- empty states ---------- */
.empty {
  display: flex; flex-direction: column; align-items: center; text-align: center; gap: 0.5rem;
  padding: 3rem 1.5rem; color: var(--muted);
}
.empty .icon { width: 2.4rem; height: 2.4rem; padding: 0.6rem; box-sizing: content-box;
  color: var(--blue); background: var(--blue-soft); border-radius: 50%; margin-bottom: 0.4rem; }
.empty strong { color: var(--blue-deep); font-size: 1.05rem; }
.empty.compact { padding: 1.25rem 0.5rem; }
.empty.compact .icon { width: 1.4rem; height: 1.4rem; padding: 0.4rem; }

/* ---------- facts (model page and home) ---------- */
.facts { display: flex; flex-direction: column; gap: 0.9rem; }
.fact { display: block; }
.fact strong {
  display: inline-block; margin-left: 0.35rem; padding: 0.15rem 0.75rem; border-radius: 999px;
  border: 1px solid var(--blue-line);
  font-size: 0.85rem; color: var(--blue-deep); background: var(--blue-soft);
}
.fact-on strong { color: var(--red-deep); background: var(--red-soft);
  border-color: var(--red-line); }
.model-name { font-size: 1.35rem; font-weight: 620; letter-spacing: -0.02em;
  color: var(--blue-deep); }

/* ---------- root cards ---------- */
.root-card { display: flex; flex-direction: column; gap: 1.1rem; }
.root-path { font-size: 1.05rem; font-weight: 600; color: var(--blue-deep);
  overflow-wrap: anywhere; }
.root-path code { font-size: 0.95em; padding: 0.2em 0.6em; }
.subhead { font-size: 0.75rem; font-weight: 620; letter-spacing: 0.07em; text-transform: uppercase;
  color: var(--muted); margin-bottom: 0.2rem; }
.root-card .card-head { margin-bottom: 0; }

/* ---------- help block ("Como usar esta página"): CSS-only <details> ---------- */
.help-block {
  max-width: 52rem; border: 1px solid var(--blue-line); border-radius: var(--radius-md);
  background: var(--blue-soft); color: var(--blue-deep); transition: var(--fade);
}
.help-block summary {
  list-style: none; cursor: pointer; display: flex; align-items: center; gap: 0.6rem;
  padding: 0.65rem 1rem; font-size: 0.95rem; font-weight: 600; border-radius: var(--radius-md);
}
.help-block summary::-webkit-details-marker { display: none; }
.help-block summary .icon { color: var(--blue); }
.help-block summary::after {
  content: ""; margin-left: auto; width: 0.5rem; height: 0.5rem; flex: none;
  border: solid currentColor; border-width: 0 2px 2px 0; transform: rotate(45deg);
  transition: transform 0.2s var(--ease);
}
.help-block[open] summary::after { transform: rotate(-135deg); }
.help-body { padding: 0 1rem 1rem 2.6rem; color: var(--ink); }
.help-body p + p { margin-top: 0.6rem; }
.help-links { display: flex; flex-wrap: wrap; gap: 0.4rem 1.25rem; font-size: 0.92rem;
  font-weight: 560; }

/* ---------- manual ---------- */
a.card.manual-cta { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  border-color: var(--blue-line); background: var(--blue-soft); }
.manual-cta .icon { width: 2rem; height: 2rem; color: var(--blue); }
.manual-cta > div { flex: 1 1 16rem; }
.manual-cta .cta-title { display: block; font-size: 1.1rem; color: var(--blue-deep); }
.manual-cta p { color: var(--ink); font-size: 0.95rem; }
.manual-cta .more { font-size: 0.9rem; color: var(--blue); font-weight: 580; }
.manual { display: grid; gap: 1.25rem; align-items: start; grid-template-columns: minmax(0, 1fr); }
.manual-toc-wide { display: none; }
.manual-toc-narrow {
  background: var(--surface); border: 1px solid var(--hairline); border-radius: var(--radius-md);
  box-shadow: var(--shadow-soft); padding: 0; transition: var(--fade);
}
.manual-toc-narrow summary {
  list-style: none; cursor: pointer; display: flex; align-items: center; gap: 0.6rem;
  padding: 0.8rem 1rem; font-weight: 620; color: var(--blue-deep);
}
.manual-toc-narrow summary::-webkit-details-marker { display: none; }
.manual-toc-narrow summary .icon { color: var(--blue); }
.manual-toc-narrow summary::after {
  content: ""; margin-left: auto; width: 0.5rem; height: 0.5rem; flex: none;
  border: solid currentColor; border-width: 0 2px 2px 0; transform: rotate(45deg);
  transition: transform 0.2s var(--ease);
}
.manual-toc-narrow[open] summary::after { transform: rotate(-135deg); }
.manual-toc-narrow .toc-list { padding: 0 0.5rem 0.75rem; }
.toc-title { font-size: 0.75rem; font-weight: 620; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted); padding: 0 0.6rem 0.5rem; }
.toc-list { list-style: none; margin: 0; padding: 0; counter-reset: toc;
  display: flex; flex-direction: column; gap: 0.1rem; }
.toc-list a {
  display: flex; align-items: center; gap: 0.65rem; padding: 0.4rem 0.6rem;
  border-radius: var(--radius-md); font-size: 0.92rem; font-weight: 540; line-height: 1.3;
  color: var(--muted); text-decoration: none; transition: var(--fade);
}
.toc-list a::before {
  counter-increment: toc; content: counter(toc); flex: none; display: grid; place-items: center;
  width: 1.5rem; height: 1.5rem; border-radius: 50%; font-size: 0.78rem; font-weight: 640;
  color: var(--blue-deep); background: var(--blue-soft); border: 1px solid var(--blue-line);
}
.toc-list a:hover { color: var(--blue-deep); background: var(--blue-soft); }
.manual-body { display: flex; flex-direction: column; gap: 1.5rem; min-width: 0; max-width: 52rem;
  counter-reset: chapter; }
.chapter { counter-increment: chapter; scroll-margin-top: var(--anchor-offset); }
.chapter-head { display: flex; align-items: center; gap: 1rem; margin-bottom: 1.25rem;
  padding-bottom: 1rem; border-bottom: 1px solid var(--hairline); }
.chapter-head h2 { font-size: clamp(1.3rem, 1.1rem + 1vw, 1.65rem); font-weight: 650; }
.chapter-num {
  flex: none; display: grid; place-items: center; width: 2.6rem; height: 2.6rem;
  border-radius: 50%; color: var(--on-accent); background: var(--blue-solid);
  box-shadow: inset 0 0 0 1px rgba(214, 178, 94, 0.6); font-size: 1.1rem; font-weight: 650;
}
.chapter-num::before { content: counter(chapter); }
.chapter-lead { color: var(--muted); margin-top: 0.2rem; }
.prose { font-size: 1.02rem; line-height: 1.65; overflow-wrap: anywhere; }
.prose h3, .chapter h3 {
  display: flex; align-items: center; gap: 0.6rem; margin: 1.75rem 0 0.7rem;
  font-size: 1.1rem; font-weight: 640; scroll-margin-top: var(--anchor-offset);
}
.prose h3::before { content: ""; flex: none; width: 1.1rem; height: 1px; background: var(--gold); }
.prose > h3:first-child { margin-top: 0; }
.prose p + p, .prose ul + p, .prose p + ul, .prose p + ol, .prose p + dl, .prose p + pre,
.prose p + .problems { margin-top: 0.75rem; }
.prose ul { margin: 0; padding-left: 1.25rem; display: flex; flex-direction: column; gap: 0.45rem; }
.prose ul ::marker { color: var(--blue); }
.prose ul.plain-list { gap: 0.8rem; }
.prose li strong:first-child { color: var(--blue-deep); }
.term { color: var(--blue); text-decoration-style: dotted; text-decoration-thickness: 1px;
  text-decoration-color: var(--gold); }
.step-link { margin-left: 0.6rem; font-size: 0.85rem; font-weight: 520; }
.steps { list-style: none; margin: 0.75rem 0 0; padding: 0; counter-reset: step;
  display: flex; flex-direction: column; gap: 0.7rem; }
.steps > li {
  position: relative; counter-increment: step; padding: 0.95rem 1rem 0.95rem 3.5rem;
  background: var(--surface-2); border: 1px solid var(--hairline); border-radius: var(--radius-md);
  transition: var(--fade);
}
.steps > li::before {
  content: counter(step); position: absolute; left: 0.9rem; top: 0.9rem;
  display: grid; place-items: center; width: 1.9rem; height: 1.9rem; border-radius: 50%;
  font-size: 0.95rem; font-weight: 650; color: var(--on-accent); background: var(--blue-solid);
  box-shadow: inset 0 0 0 1px rgba(214, 178, 94, 0.55);
}
.step-title { display: block; margin-bottom: 0.2rem; font-weight: 640; color: var(--blue-deep);
  line-height: 1.35; }
.example { display: block; margin-top: 0.45rem; padding: 0.4rem 0.75rem; font-size: 0.93rem;
  color: var(--muted); background: var(--surface); border-left: 2px solid var(--gold);
  border-radius: 0 var(--radius-md) var(--radius-md) 0; overflow-wrap: anywhere; }
.example strong { color: var(--blue-deep); }
.callout {
  position: relative; display: flex; gap: 0.8rem; align-items: flex-start; margin: 1.1rem 0;
  padding: 0.95rem 1.1rem; border: 1px solid; border-radius: var(--radius-md);
  transition: var(--fade);
}
.callout .icon { margin-top: 0.2rem; }
.callout-title { display: block; margin-bottom: 0.15rem; }
.callout p + p { margin-top: 0.45rem; }
.callout-tip { background: var(--blue-soft); border-color: var(--blue-line); color: var(--ink); }
.callout-tip .icon, .callout-tip .callout-title { color: var(--blue); }
.callout-warn { background: var(--red-soft); border-color: var(--red-line); color: var(--ink); }
.callout-warn .icon, .callout-warn .callout-title { color: var(--red); }
.callout-warn::before { content: ""; position: absolute; left: 1rem; right: 1rem; top: -1px;
  height: 1px; background: linear-gradient(90deg, transparent, var(--gold), transparent); }
.callout code { background: var(--surface); }
.tree-figure { margin: 0; }
.tree-figure figcaption { margin: 0 0 0.5rem; }
.tree {
  margin: 0.75rem 0 0; padding: 0.9rem 1rem; overflow-x: auto; white-space: pre;
  font-family: var(--mono); font-size: 0.86rem; line-height: 1.55; color: var(--blue-deep);
  background: var(--surface-2); border: 1px solid var(--hairline); border-radius: var(--radius-md);
}
.roles { margin: 0.75rem 0 0; display: grid; gap: 0.6rem; }
.roles > div { padding: 0.75rem 1rem; background: var(--surface-2); border: 1px solid
  var(--hairline); border-radius: var(--radius-md); }
.roles dt { font-weight: 640; color: var(--blue-deep); }
.roles dd { margin: 0.15rem 0 0; color: var(--muted); font-size: 0.95rem; }
.problems { display: flex; flex-direction: column; gap: 0.8rem; margin-top: 0.75rem; }
.problem {
  position: relative; padding: 0.9rem 1.1rem 0.95rem; background: var(--surface);
  border: 1px solid var(--hairline); border-left: 3px solid var(--red); border-radius:
  var(--radius-md); scroll-margin-top: var(--anchor-offset); transition: var(--fade);
}
.problem p + p { margin-top: 0.4rem; }
.problem ul { margin-top: 0.4rem; }
.problem strong { color: var(--blue-deep); }
.problem-aviso { border-left-color: var(--blue-solid); }
.problem-situacao { border-left-color: var(--gold); }
.problem-msg { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.7rem; }
.problem-tag {
  flex: none; padding: 0.1rem 0.6rem; border-radius: 999px; border: 1px solid var(--red-line);
  font-size: 0.72rem; font-weight: 640; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--red-deep); background: var(--red-soft);
}
.problem-aviso .problem-tag { color: var(--blue-deep); background: var(--blue-soft);
  border-color: var(--blue-line); }
.problem-tag-situacao { color: var(--muted); background: var(--surface-2);
  border-color: var(--hairline-strong); }
.problem-msg code { font-size: 0.9rem; color: var(--red-deep); background: var(--red-soft);
  white-space: normal; }
.problem-aviso .problem-msg code { color: var(--blue-deep); background: var(--blue-soft); }
.glossary { margin: 0; display: grid; gap: 0.75rem; grid-template-columns: minmax(0, 1fr); }
.term-card { padding: 0.9rem 1rem; background: var(--surface-2); border: 1px solid var(--hairline);
  border-radius: var(--radius-md); scroll-margin-top: var(--anchor-offset);
  transition: var(--fade); }
.term-card dt { font-weight: 650; color: var(--blue-deep); }
.term-card dd { margin: 0.2rem 0 0; color: var(--muted); font-size: 0.97rem; line-height: 1.55; }
.back-top-row { margin-top: 1.5rem; }
.back-top { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.9rem;
  font-weight: 540; text-decoration: none; color: var(--muted); }
.back-top:hover { color: var(--blue); }

/* ---------- motion ---------- */
@keyframes rise {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: none; }
}
/* fill-mode backwards: after the entrance the normal styles apply again, so hover lift works */
.page-head, .notice, .alert, .back, .card, .split > *, .grid > *, .stack > * {
  animation: rise 0.55s var(--ease) backwards;
}
main > :nth-child(2), .grid > :nth-child(2), .stack > :nth-child(2), .split > :nth-child(2) {
  animation-delay: 0.06s; }
main > :nth-child(3), .grid > :nth-child(3), .stack > :nth-child(3) { animation-delay: 0.12s; }
main > :nth-child(4), .grid > :nth-child(4), .stack > :nth-child(4) { animation-delay: 0.18s; }
main > :nth-child(n + 5), .grid > :nth-child(n + 5), .stack > :nth-child(n + 5) {
  animation-delay: 0.24s; }

/* ---------- responsive ---------- */
@media (max-width: 519px) {
  .nav { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.2rem; }
  .nav a { flex-direction: column; justify-content: center; text-align: center;
    padding: 0.45rem 0.15rem 0.55rem; gap: 0.15rem; font-size: 0.78rem; }
  .nav a::after { left: 0.6rem; right: 0.6rem; }
  .help-body { padding-left: 1rem; }
  .chapter-head { align-items: flex-start; }
  .chapter-num { width: 2.2rem; height: 2.2rem; font-size: 1rem; }
  .steps > li { padding-left: 3.1rem; }
  .steps > li::before { left: 0.7rem; }
}
@media (min-width: 520px) and (max-width: 899px) {
  :root { --anchor-offset: 7rem; } /* the compact top bar is sticky here */
  .sidebar { position: sticky; top: 0; }
  .nav a { padding: 0.4rem 0.7rem; }
}
@media (max-width: 760px) {
  .brand-sub { display: none; }
  table.stack-sm, table.stack-sm tbody { display: block; }
  table.stack-sm thead { position: absolute; width: 1px; height: 1px; overflow: hidden;
    clip-path: inset(50%); }
  table.stack-sm tr { display: block; padding: 0.9rem 1.15rem; border-top: 1px solid
    var(--hairline); }
  table.stack-sm tbody tr:first-child { border-top: 0; }
  table.stack-sm td { display: flex; justify-content: space-between; align-items: center; gap: 1rem;
    padding: 0.35rem 0; border: 0; text-align: right; }
  table.stack-sm td[data-label]::before {
    content: attr(data-label); flex: none; text-align: left;
    font-size: 0.72rem; font-weight: 620; letter-spacing: 0.07em; text-transform: uppercase;
    color: var(--muted);
  }
  table.stack-sm td.cell-form { display: block; text-align: left; }
  table.stack-sm td.num { width: auto; }
  table.stack-sm td.actions-cell { justify-content: flex-start; white-space: normal; }
  .actions { flex-wrap: wrap; }
  td.cell-form > form { flex-direction: column; align-items: stretch; }
  td.cell-form label:not(.check) { flex: none; }
  .empty { padding: 2.25rem 1rem; }
}
@media (min-width: 620px) {
  .form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (min-width: 640px) {
  main { padding: 2rem 1.5rem 3rem; gap: 1.5rem; }
  .footer { padding: 1.5rem 1.5rem 2rem; }
  .card { padding: 1.5rem; }
  .card::before { left: 1.5rem; right: 1.5rem; }
  .card.flush { padding: 0; }
  .card.flush::before { left: 0; right: 0; }
  .notice { padding: 1.35rem 1.5rem; }
  .stack, .split { gap: 1.5rem; }
}
@media (min-width: 900px) {
  .shell { display: grid; grid-template-columns: 16rem minmax(0, 1fr); align-items: start; }
  .sidebar {
    position: sticky; top: 0; height: 100vh; overflow-y: auto;
    display: flex; flex-direction: column; align-items: stretch; gap: 1.75rem;
    padding: 1.5rem 1rem 1.25rem;
    border-bottom: 0; border-right: 1px solid var(--hairline);
    box-shadow: 8px 0 40px -28px rgba(18, 23, 43, 0.3);
  }
  .sidebar::after {
    left: auto; right: -1px; top: 0; bottom: 0; width: 1px; height: auto;
    background: linear-gradient(180deg, transparent, var(--gold) 15%, var(--gold) 55%, transparent);
  }
  .brand { padding: 0 0.5rem; }
  .brand svg { width: 2.4rem; height: 2.4rem; }
  .nav { flex-direction: column; flex-wrap: nowrap; gap: 0.25rem; }
  .nav a { padding: 0.65rem 0.85rem; }
  .nav a::before {
    content: ""; position: absolute; left: 0; top: 22%; bottom: 22%; width: 2px; border-radius: 2px;
    background: var(--gold); opacity: 0; transform: scaleY(0.4);
    transition: opacity 0.25s var(--ease), transform 0.25s var(--ease);
  }
  .nav a::after { display: none; }
  .nav a.nav-help { margin-top: 0.9rem; }
  .nav a:hover { transform: translateX(3px); }
  .nav a:hover::before { opacity: 0.6; transform: none; }
  .nav a[aria-current="page"]::before { opacity: 1; transform: none; }
  .side-foot { margin-top: auto; justify-content: space-between; padding: 1rem 0.5rem 0;
    border-top: 1px solid var(--hairline); transition: border-color 0.35s var(--ease); }
  .side-label { display: block; }
  main { padding: 2.5rem 2.5rem 3rem; }
  .footer { padding: 1.5rem 2.5rem 2rem; }
  td code { overflow-wrap: normal; white-space: nowrap; }
}
@media (min-width: 760px) {
  .glossary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .roles { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (min-width: 1200px) {
  .manual { grid-template-columns: 15.5rem minmax(0, 1fr); gap: 2rem; }
  .manual-toc-narrow { display: none; }
  .manual-toc-wide {
    display: block; position: sticky; top: 1.5rem; max-height: calc(100vh - 3rem);
    overflow-y: auto; padding: 1rem 0.5rem; background: var(--surface);
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
@media (min-width: 900px) and (max-width: 1359px) {
  /* the sidebar leaves less room: row forms wrap instead of squeezing their fields */
  td.cell-form > form { flex-wrap: wrap; }
  td.cell-form label:not(.check) { flex: 1 1 10.5rem; }
}
@media (min-width: 1360px) and (max-width: 1479px) {
  .split.split-table { grid-template-columns: minmax(0, 1fr); }
  .split.split-table > .aside .form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (min-width: 1500px) {
  /* thin gold hairlines mark the edges of the content column when the margins grow */
  main::before, main::after {
    content: ""; position: absolute; top: 2.5rem; bottom: 3rem; width: 1px; pointer-events: none;
    background: linear-gradient(180deg, transparent, var(--gold) 18%, transparent 70%);
    opacity: 0.4;
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
    --gold: #666; --on-accent: #fff; --shadow-soft: none; --shadow-lift: none;
    color-scheme: light;
  }
  html, body { background: #fff !important; color: #000 !important; }
  body::before, body::after, .card::before, .callout::before, main::before, main::after {
    display: none !important;
  }
  .sidebar, .skip, .manual-toc-narrow, .back-top-row, .help-block, .footer, .theme-switch {
    display: none !important;
  }
  .manual-toc-wide {
    display: block !important; position: static; max-height: none; overflow: visible;
    border: 1px solid #999; box-shadow: none; margin-bottom: 1rem; break-after: page;
  }
  .shell, .manual { display: block; }
  main { max-width: none; padding: 0; gap: 0.8rem; }
  .manual-body { max-width: none; gap: 1rem; }
  .card, .callout, .problem, .steps > li, .term-card, .tree, .example {
    box-shadow: none !important; background: #fff !important; border-color: #999 !important;
    color: #000 !important; break-inside: avoid;
  }
  .card { padding: 0.5rem 0; border: 0 !important; }
  h1, h2, h3 { color: #000 !important; break-after: avoid; }
  a { color: #000; text-decoration: underline; }
  code { color: #000 !important; background: #fff !important; border: 1px solid #999; }
  .chapter-num, .steps > li::before { background: #fff !important; color: #000 !important;
    border: 1px solid #000; box-shadow: none; }
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
  .card.lift:hover, a.card:hover, button:active, .nav a:hover, a.card:hover .more,
  .brand:hover svg { transform: none; }
}
"""

CSS: Final = _CSS.replace("/*DARK*/", _DARK_TOKENS)
