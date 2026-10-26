"""Self-hosted assets: allowlisted routes, CSP, manifest hashes, Phosphor icons, Lottie files.

Portable: no database. The CSP string below is a second, independent copy on purpose: adding
the icons, the Lottie library and the animations must not loosen the policy by a single token.
"""

from __future__ import annotations

import builtins
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from osa.core.settings import Settings
from osa.panel import app as panel
from osa.panel import assets, icons

PORT = 8765
ROOT = Path(__file__).resolve().parents[2]
STATIC = assets.STATIC_DIR
TEMPLATES = ROOT / "src" / "osa" / "panel" / "templates"
LOOPBACK = ("127.0.0.1", 50000)

EXPECTED_CSP = (
    "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
# The font URLs carry the first ten hex digits of the file's sha256, so they are read from the
# manifest here; the stems (which files may be served at all) are spelled out as a second copy.
FONT_STEMS = {
    "ibm-plex-sans-400-latin1",
    "ibm-plex-sans-400-latin2",
    "ibm-plex-sans-500-latin1",
    "ibm-plex-sans-500-latin2",
    "ibm-plex-sans-600-latin1",
    "ibm-plex-sans-600-latin2",
    "ibm-plex-serif-600-latin1",
    "ibm-plex-serif-600-latin2",
    "ibm-plex-mono-400-latin1",
}
IMMUTABLE_CACHE = "private, max-age=31536000, immutable"
STATIC_PATHS = {
    "/motion.js",
    "/favicon.svg",
    "/assets/lottie.min.js",
    "/assets/lottie/check.json",
    "/assets/lottie/spinner.json",
    "/assets/lottie/theme.json",
}
LOTTIE_FRAMES = {"check": 50, "spinner": 30, "theme": 49}
LOOPS = ("spinner",)


def make_client(*, client: tuple[str, int] = LOOPBACK) -> TestClient:
    application = panel.create_app(session_factory=lambda: None, settings=Settings(), port=PORT)
    return TestClient(
        application,
        base_url=f"http://127.0.0.1:{PORT}",
        client=client,
        raise_server_exceptions=False,
    )


def manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((STATIC / "MANIFEST.json").read_text(encoding="utf-8"))
    return data


def _font_paths() -> dict[str, str]:
    paths = {}
    for stem in FONT_STEMS:
        digest = manifest()["files"][f"fonts/{stem}.woff2"]["sha256"]
        paths[stem] = f"/fonts/{stem}.{digest[:10]}.woff2"
    return paths


FONT_PATHS = _font_paths()
EXPECTED_PATHS = STATIC_PATHS | set(FONT_PATHS.values())


# --- routes: exact allowlist --------------------------------------------------------


def test_the_allowlist_is_exactly_the_documented_set() -> None:
    assert {asset.path for asset in assets.ASSETS} == EXPECTED_PATHS


@pytest.mark.parametrize("asset", assets.ASSETS, ids=lambda a: a.path)
def test_each_asset_is_served_with_its_type_nosniff_and_the_panel_headers(
    asset: assets.Asset,
) -> None:
    response = make_client().get(asset.path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(asset.content_type)
    assert response.headers["x-content-type-options"] == "nosniff"
    # the same policy as /panel.css, except the hashed fonts (see the font tests below)
    expected_cache = IMMUTABLE_CACHE if asset.path.startswith("/fonts/") else "no-store"
    assert response.headers["cache-control"] == expected_cache
    assert response.headers["content-security-policy"] == EXPECTED_CSP
    assert response.headers["x-frame-options"] == "DENY"
    assert response.content == (STATIC / asset.file).read_bytes()


def test_content_types_are_the_expected_ones() -> None:
    client = make_client()

    assert client.get("/motion.js").headers["content-type"].startswith("text/javascript")
    assert client.get("/assets/lottie.min.js").headers["content-type"].startswith("text/javascript")
    assert client.get("/assets/lottie/check.json").headers["content-type"] == "application/json"
    assert client.get("/favicon.svg").headers["content-type"] == "image/svg+xml"
    for path in FONT_PATHS.values():
        assert client.get(path).headers["content-type"] == "font/woff2"


TRAVERSAL = [
    "/assets/../MANIFEST.json",
    "/assets/%2e%2e/MANIFEST.json",
    "/assets/%2E%2E%2FMANIFEST.json",
    "/assets/lottie/../../MANIFEST.json",
    "/assets/lottie/..%2f..%2fmotion.js",
    "/assets/lottie/%2e%2e/%2e%2e/MANIFEST.json",
    "/assets/lottie/check.json%00.png",
    "/assets/lottie/nope.json",
    "/assets/lottie/CHECK.json",
    "/assets/lottie/check.JSON",
    "/assets/lottie/check",
    "/assets/lottie/",
    "/assets/lottie",
    "/assets/",
    "/assets",
    "/assets/MANIFEST.json",
    "/assets/vendor/lottie_light.min.js",
    "/assets/lottie.min.js.map",
    "/assets/%2Fetc%2Fpasswd",
    "/assets//etc/passwd",
    "//etc/passwd",
    "/static/MANIFEST.json",
    "/static/",
    "/MANIFEST.json",
    "/icons/house.svg",
    "/assets/icons/house.svg",
    "/motion.js.map",
    "/motion.JS",
    "/favicon.ico",
    "/favicon.svg/x",
    # fonts: only the exact hashed names are routes
    "/fonts",
    "/fonts/",
    "/fonts/../MANIFEST.json",
    "/fonts/%2e%2e/MANIFEST.json",
    "/fonts/..%2fMANIFEST.json",
    "/fonts/ibm-plex-sans-400-latin1.woff2",  # no hash
    "/fonts/ibm-plex-sans-400-latin1.0000000000.woff2",  # wrong hash
    "/fonts/OFL-ibm-plex-sans.txt",  # shipped, never served
    "/fonts/OFL-ibm-plex-serif.txt",
    "/fonts/OFL-ibm-plex-mono.txt",
    "/fonts/ibm-plex-mono-400-latin1.woff2%00.png",
    "/fonts/nope.woff2",
    "/assets/fonts/ibm-plex-mono-400-latin1.woff2",
    "/static/fonts/ibm-plex-mono-400-latin1.woff2",
    "/fonts/ibm-plex-mono-400-latin1.woff2.map",
    # the earlier third-party builds are gone
    "/fonts/ibm-plex-sans-latin-wght-normal.woff2",
    "/fonts/ibm-plex-mono-latin-400-normal.woff2",
    # the previous typefaces are gone: no route, no file
    "/fonts/instrument-sans-latin-wght-normal.2ee17598ab.woff2",
    "/fonts/geist-mono-latin-400-normal.3f98383b12.woff2",
    *(path.upper() for path in FONT_PATHS.values()),
    *(path.replace("/fonts/", "/fonts//") for path in FONT_PATHS.values()),
]


@pytest.mark.parametrize("path", TRAVERSAL)
def test_unknown_names_traversal_and_directory_urls_are_404(path: str) -> None:
    response = make_client().get(path)

    assert response.status_code == 404, path
    assert "sha256" not in response.text and "phosphor" not in response.text.lower()
    assert not response.headers["content-type"].startswith(("text/javascript", "image/svg"))


@pytest.mark.parametrize(
    ("path", "file"),
    [
        ("/assets%2Flottie%2Fcheck.json", "lottie/check.json"),
        ("/assets/lottie%2Fcheck.json", "lottie/check.json"),
        ("/assets%2Flottie.min.js", "vendor/lottie_light.min.js"),
    ],
)
def test_encoded_slashes_reach_only_the_same_exact_asset(path: str, file: str) -> None:
    # Documented expectation: the server decodes %2F before routing, so an encoded slash names
    # the same exact route. Harmless (a fixed table, no filesystem), pinned so a change shows.
    response = make_client().get(path)

    assert response.status_code == 200
    assert response.content == (STATIC / file).read_bytes()


def test_a_trailing_slash_never_leads_anywhere_but_the_same_asset() -> None:
    response = make_client().get("/assets/lottie/check.json/", follow_redirects=False)

    assert response.status_code in (404, 307)
    if response.status_code == 307:
        assert response.headers["location"].endswith("/assets/lottie/check.json")


@pytest.mark.parametrize("path", sorted(FONT_PATHS.values()))
def test_a_trailing_slash_on_a_font_leads_only_to_the_same_exact_font(path: str) -> None:
    response = make_client().get(path + "/", follow_redirects=False)

    assert response.status_code in (404, 307)
    if response.status_code == 307:
        assert response.headers["location"].endswith(path)
        assert response.headers["cache-control"] == "no-store"  # a redirect is never cached


@pytest.mark.parametrize(
    "query",
    ["?name=../../MANIFEST.json", "?file=/etc/passwd", "?f=nope.json", "?path=..%2fmotion.js"],
)
def test_a_query_string_never_selects_a_file(query: str) -> None:
    client = make_client()

    response = client.get("/assets/lottie/check.json" + query)

    assert response.status_code == 200
    assert response.content == (STATIC / "lottie" / "check.json").read_bytes()


def test_requests_never_read_the_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client()  # the bytes were read once, here, from the package directory

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a request touched the filesystem")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(Path, "open", refuse)
    monkeypatch.setattr(Path, "stat", refuse)

    for path in (*TRAVERSAL, *EXPECTED_PATHS):
        status = client.get(path).status_code
        assert status == (200 if path in EXPECTED_PATHS else 404), path


def test_assets_are_found_through_the_package_not_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # an empty directory: nothing relative could resolve

    assert make_client().get("/assets/lottie/check.json").status_code == 200
    assert STATIC.is_absolute() and STATIC == Path(panel.__file__).resolve().parent / "static"


@pytest.mark.parametrize("path", sorted(EXPECTED_PATHS))
@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1:9999", ""])
def test_assets_are_refused_to_a_forged_host(path: str, host: str) -> None:
    response = make_client().get(path, headers={"Host": host})

    assert response.status_code == 403
    assert response.text == "forbidden"
    assert response.headers["content-security-policy"] == EXPECTED_CSP


@pytest.mark.parametrize("path", sorted(EXPECTED_PATHS))
def test_assets_are_refused_to_a_non_loopback_peer(path: str) -> None:
    assert make_client(client=("192.0.2.10", 50000)).get(path).status_code == 403


def test_assets_cannot_be_posted_to() -> None:
    client = make_client()
    assert client.get("/panel.css").status_code == 200
    token = client.cookies.get("osa_csrf") or ""

    refused = client.post("/motion.js", data={"csrf_token": token})
    wrong_method = client.post(
        "/motion.js", data={"csrf_token": token}, headers={"Origin": f"http://127.0.0.1:{PORT}"}
    )

    assert refused.status_code == 403
    assert wrong_method.status_code == 405


# --- fonts: self-hosted, hashed, immutable --------------------------------------------


def test_the_font_allowlist_is_exactly_nine_hashed_woff2_files() -> None:
    served = {a.path for a in assets.ASSETS if a.path.startswith("/fonts/")}

    assert served == set(FONT_PATHS.values()) and len(served) == 9
    for stem, path in FONT_PATHS.items():
        digest = manifest()["files"][f"fonts/{stem}.woff2"]["sha256"]
        assert re.fullmatch(rf"/fonts/{re.escape(stem)}\.[0-9a-f]{{10}}\.woff2", path)
        assert path.split(".")[-2] == digest[:10]
    assert {a.path for a in assets.ASSETS if a.immutable} == served
    assert assets.IMMUTABLE_PATHS == frozenset(served)
    assert all(a.content_type == "font/woff2" for a in assets.ASSETS if a.immutable)


@pytest.mark.parametrize("stem", sorted(FONT_STEMS))
def test_each_font_is_the_manifest_file_served_as_woff2_with_the_panel_headers(stem: str) -> None:
    response = make_client().get(FONT_PATHS[stem])
    expected = (STATIC / "fonts" / f"{stem}.woff2").read_bytes()

    assert response.status_code == 200
    assert response.headers["content-type"] == "font/woff2"
    assert response.content == expected and expected[:4] == b"wOF2"
    assert (
        hashlib.sha256(expected).hexdigest() == manifest()["files"][f"fonts/{stem}.woff2"]["sha256"]
    )
    for name, value in panel.SECURITY_HEADERS.items():
        if name != "cache-control":
            assert response.headers[name] == value, name
    assert response.headers["content-security-policy"] == EXPECTED_CSP


def test_only_the_hashed_fonts_may_be_cached_and_everything_else_stays_no_store() -> None:
    client = make_client()
    other = {"/panel.css", "/theme.js", "/manual", "/nope", *STATIC_PATHS}

    for path in FONT_PATHS.values():
        assert client.get(path).headers["cache-control"] == IMMUTABLE_CACHE
    assert "no-cache" not in IMMUTABLE_CACHE and IMMUTABLE_CACHE.startswith("private,")
    for path in sorted(other):
        assert client.get(path).headers["cache-control"] == "no-store", path
    # refusals, wrong methods, unknown names and redirects around a font are never cached
    font = next(iter(FONT_PATHS.values()))
    forged = client.get(font, headers={"Host": "evil.example"})
    assert (forged.status_code, forged.headers["cache-control"]) == (403, "no-store")
    foreign = make_client(client=("192.0.2.10", 50000)).get(font)
    assert (foreign.status_code, foreign.headers["cache-control"]) == (403, "no-store")
    posted = client.post(font)
    assert posted.status_code in (403, 405) and posted.headers["cache-control"] == "no-store"
    head = client.head(font)
    assert head.headers["cache-control"] == "no-store"  # not a route: refused, never cached
    missing = client.get(font.replace(".woff2", "x.woff2"))
    assert (missing.status_code, missing.headers["cache-control"]) == (404, "no-store")


def test_font_responses_differ_from_the_page_headers_in_cache_control_only() -> None:
    client = make_client()
    page = client.get("/panel.css").headers
    font = client.get(next(iter(FONT_PATHS.values()))).headers

    for name, value in panel.SECURITY_HEADERS.items():
        assert page[name] == value
        if name != "cache-control":
            assert font[name] == value


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1:9999", ""])
def test_fonts_are_refused_to_a_forged_host_and_a_non_loopback_peer(host: str) -> None:
    for path in FONT_PATHS.values():
        assert make_client().get(path, headers={"Host": host}).status_code == 403
        assert make_client(client=("192.0.2.10", 50000)).get(path).status_code == 403


def test_the_csp_has_no_font_src_so_fonts_fall_back_to_default_src_self() -> None:
    assert "font-src" not in panel.SECURITY_HEADERS["content-security-policy"]
    assert "default-src 'self'" in panel.SECURITY_HEADERS["content-security-policy"]
    assert panel.SECURITY_HEADERS["content-security-policy"] == EXPECTED_CSP


def test_a_tampered_or_missing_font_stops_the_panel_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _copy_static(tmp_path)
    victim = target / "fonts" / "ibm-plex-mono-400-latin1.woff2"
    victim.write_bytes(victim.read_bytes() + b"\x00")
    monkeypatch.setattr(assets, "STATIC_DIR", target)
    with pytest.raises(assets.AssetIntegrityError, match=r"ibm-plex-mono-400-latin1"):
        make_client()

    victim.unlink()
    with pytest.raises(assets.AssetIntegrityError, match="missing"):
        make_client()


def test_a_font_missing_from_the_manifest_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _copy_static(tmp_path)
    data = json.loads((target / "MANIFEST.json").read_text())
    del data["files"]["fonts/ibm-plex-sans-400-latin1.woff2"]
    (target / "MANIFEST.json").write_text(json.dumps(data))
    monkeypatch.setattr(assets, "STATIC_DIR", target)

    with pytest.raises(assets.AssetIntegrityError, match="not in the manifest"):
        assets._font_paths()


LICENCES = ("OFL-ibm-plex-sans.txt", "OFL-ibm-plex-serif.txt", "OFL-ibm-plex-mono.txt")


def test_fonts_are_shipped_byte_for_byte_with_their_licence_and_inside_the_budget() -> None:
    files = manifest()["files"]
    sources = manifest()["sources"]
    fonts = {k: v for k, v in files.items() if k.startswith("fonts/")}

    assert set(fonts) == {
        *(f"fonts/{stem}.woff2" for stem in FONT_STEMS),
        *(f"fonts/{name}" for name in LICENCES),
    }
    assert {v["origin"] for v in fonts.values()} == {
        "plex-sans", "plex-serif", "plex-mono",
        "plex-sans-licence", "plex-serif-licence", "plex-mono-licence",
    }  # fmt: skip
    # nothing left over from an earlier typeface
    assert {p.name for p in (STATIC / "fonts").iterdir()} == {
        *(f"{stem}.woff2" for stem in FONT_STEMS),
        *LICENCES,
    }
    # first load: the Latin1 file of every face (sans 400/500/600, serif 600, mono 400); the
    # Latin2 (Latin-extended) ones are unicode-range lazy
    first_load = sum(
        (STATIC / f"fonts/{stem}.woff2").stat().st_size
        for stem in FONT_STEMS
        if stem.endswith("-latin1")
    )
    assert first_load <= 120 * 1024, first_load
    for licence in LICENCES:
        text = (STATIC / "fonts" / licence).read_text(encoding="utf-8")
        # IBM's own LICENSE.txt, in full: the copyright and Reserved Font Name line and the OFL
        assert 'Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"' in text
        assert "SIL OPEN FONT LICENSE Version 1.1" in text
    assert sources["plex-sans"]["font_version"] == "3.005"
    assert sources["plex-serif"]["font_version"] == "3.006"
    assert sources["plex-mono"]["font_version"] == "2.005"


def test_the_stylesheet_loads_the_fonts_by_hash_with_swap_and_metric_fallbacks() -> None:
    css = make_client().get("/panel.css").text
    faces = re.findall(r"@font-face \{(.*?)\n\}", css, re.S)

    web = [f for f in faces if "src: url(" in f]
    fallback = [f for f in faces if "src: local(" in f]
    assert len(faces) == 12 and len(web) == 9 and len(fallback) == 3
    for face in web:
        assert "font-display: swap" in face and 'format("woff2")' in face
        assert "unicode-range:" in face
    urls = re.findall(r'url\("([^"]+)"\)', css)
    assert sorted(urls) == sorted(FONT_PATHS.values())
    families = [re.search(r'font-family: "([^"]+)"', f).group(1) for f in web]  # type: ignore[union-attr]
    assert families == ["Panel Sans"] * 6 + ["Panel Serif"] * 2 + ["Panel Mono"]
    # one static weight per face (no synthesized weights), Latin1 then Latin2
    weights = [re.search(r"font-weight: (\d+);", f).group(1) for f in web]  # type: ignore[union-attr]
    assert weights == ["400", "400", "500", "500", "600", "600", "600", "600", "400"]
    for face, local in zip(fallback, ("Arial", "Georgia", "Courier New"), strict=True):
        assert f'local("{local}")' in face
        for descriptor in (
            "size-adjust",
            "ascent-override",
            "descent-override",
            "line-gap-override",
        ):
            assert descriptor in face
    assert "http://" not in css and "https://" not in css and "@import" not in css
    assert "data:" not in css
    assert "Instrument" not in css and "Geist" not in css
    # each web face comes first in its stack, then its metric-compatible fallback
    assert re.search(r'--font: "Panel Sans", "Panel Sans Fallback"', css)
    assert re.search(r'--serif: "Panel Serif", "Panel Serif Fallback"', css)
    assert re.search(r'--mono: "Panel Mono", "Panel Mono Fallback"', css)


def test_no_exposed_font_family_uses_the_reserved_font_name() -> None:
    # Upstream IBM Plex declares Reserved Font Name "Plex". The shipped files are IBM's own
    # builds (not third-party Modified Versions), but the panel still declares and asks for
    # neutral aliases only; the fonts are credited, by their real name, in the notices.
    css = make_client().get("/panel.css").text
    declared = re.findall(r"font-family:\s*([^;]+);", css)
    stacks = re.findall(r"--(?:font|serif|mono):\s*([^;]+);", css)
    assert declared and len(stacks) == 3
    for value in (*declared, *stacks):
        assert "plex" not in value.lower(), value
    assert "plex" not in css.lower().replace("/fonts/ibm-plex-", "/fonts/")


def test_every_page_preloads_the_text_face_and_the_title_face_with_crossorigin() -> None:
    page = make_client().get("/manual").text
    head = page.split("</head>")[0]

    links = re.findall(r'<link rel="preload"[^>]*>', head)
    expected = [
        FONT_PATHS["ibm-plex-sans-400-latin1"],
        FONT_PATHS["ibm-plex-serif-600-latin1"],
    ]
    assert links == [
        f'<link rel="preload" href="{path}" as="font" type="font/woff2" crossorigin>'
        for path in expected
    ]
    assert head.index('rel="preload"') < head.index("/panel.css")  # starts before the sheet


# --- CSP is untouched -----------------------------------------------------------------


def test_the_csp_header_is_byte_for_byte_the_previous_one() -> None:
    assert panel.SECURITY_HEADERS["content-security-policy"] == EXPECTED_CSP
    for token in ("unsafe", "wasm", "blob:", "data:", "http", "*", "cdn", "nonce", "hash"):
        assert token not in EXPECTED_CSP


@pytest.mark.parametrize(
    "path", ["/", "/manual", "/panel.css", "/theme.js", "/nope", *EXPECTED_PATHS]
)
def test_every_kind_of_response_carries_that_csp(path: str) -> None:
    response = make_client().get(path)

    assert response.status_code in (200, 404, 500)  # "/" has no database here: 500 page
    assert response.headers["content-security-policy"] == EXPECTED_CSP


def test_the_manual_page_carries_that_csp_and_uses_no_inline_code() -> None:
    response = make_client().get("/manual")

    assert response.headers["content-security-policy"] == EXPECTED_CSP
    page = response.text
    assert re.findall(r"<script\b[^>]*>", page) == [
        '<script src="/theme.js">',
        '<script src="/assets/lottie.min.js" defer>',
        '<script src="/motion.js" defer>',
    ]
    assert "<style" not in page and " style=" not in page
    assert not re.search(r"\son\w+\s*=", page) and "javascript:" not in page.lower()
    assert not re.search(r'(?:src|href)="https?:', page)
    assert '<link rel="icon" type="image/svg+xml" href="/favicon.svg">' in page


# --- shipped code is free of eval, remote hosts and inline styling ---------------------


def test_the_lottie_build_is_the_light_one_without_eval_or_remote_hosts() -> None:
    text = make_client().get("/assets/lottie.min.js").text

    assert not re.search(r"\beval\s*\(", text)
    assert "new Function" not in text and not re.search(r"(?<![A-Za-z])Function\(", text)
    assert "importScripts" not in text and 'setAttribute("style"' not in text
    assert "innerHTML" not in text and "document.write" not in text
    namespaces = set(re.findall(r"https?://[^\"'\s)]+", text))
    assert namespaces <= {
        "http://www.w3.org/2000/svg",
        "http://www.w3.org/1999/xlink",
        "http://www.w3.org/XML/1998/namespace",
    }, namespaces


def test_motion_script_has_no_eval_storage_remote_url_or_html_injection() -> None:
    text = make_client().get("/motion.js").text

    for forbidden in (
        "eval(",
        "new Function",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "localStorage",
        "sessionStorage",
        "XMLHttpRequest",
        "http://",
        "https://",
        'setAttribute("style"',
        "importScripts",
        "WebSocket",
    ):
        assert forbidden not in text, forbidden


def test_the_lottie_library_is_only_ever_given_inline_data() -> None:
    # The light build still contains an XHR loader (used only with `path:`) and a Blob-URL
    # worker (only after `useWebWorker(true)`); motion.js reaches neither.
    text = make_client().get("/motion.js").text

    assert "animationData: data" in text
    assert "path:" not in text and "useWebWorker" not in text and "Worker" not in text


def _keyframes(css: str, name: str) -> str:
    found = re.search(rf"@keyframes {name} \{{(.*?)\n\}}", css, re.S)
    assert found, name
    return found.group(1)


def test_page_blocks_pop_in_once_with_a_short_bounded_stagger() -> None:
    css = make_client().get("/panel.css").text

    rise = _keyframes(css, "rise")
    start = re.search(r"0% \{([^}]*)\}", rise)
    assert start and "opacity: 0" in start.group(1) and "scale(0.97)" in start.group(1)
    assert re.search(r"100% \{ opacity: 1; transform: none; \}", rise)  # ends visible, at rest
    assert re.search(r"scale\(1\.0\d+\)", rise)  # a hair of overshoot: a pop, not a slide
    assert "width" not in rise and "height" not in rise and "margin" not in rise  # no layout
    entrance = re.search(r"([^{}]*)\{\s*animation-name: rise;([^{}]*)\}", css)
    assert entrance
    selector, body = entrance.groups()
    for block in ("main >", ".split >", ".stack >", ".manual-body >"):
        assert block in selector, block
    duration = re.search(r"animation-duration: ([0-9.]+)s", body)
    assert duration and 0.3 <= float(duration.group(1)) <= 0.5
    # backwards only covers the delay ("forwards"/"both" would pin the end state); the hidden
    # state lives only in the keyframe, never in the blocks' own styles, so without the
    # animation (or once it ends) every block is visible
    assert "animation-fill-mode: backwards" in body and "forwards" not in css
    assert "both" not in body and "opacity" not in body and "visibility" not in body
    delays = [float(d) for d in re.findall(r"animation-delay: ([0-9.]+)s", css)]
    assert delays and max(delays) <= 0.25  # the whole entrance ends well under a second
    assert max(delays) + float(duration.group(1)) < 0.8
    assert "infinite" not in css  # no loop
    # linked cards lift a little on hover (restored), and reduced motion pins them in place
    assert re.search(r"\.card\.lift:hover, a\.card:hover \{\s*transform: translateY\(-3px\)", css)
    reduced = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert re.search(r"\.card\.lift:hover, a\.card:hover,[^{]*\{ transform: none; \}", reduced)


def test_menu_icons_only_pop_on_hover_press_and_page_open() -> None:
    css = make_client().get("/panel.css").text

    for name in ("wake", "nav-pop"):  # the same pop: scale up and settle, nothing else
        pop = _keyframes(css, name)
        assert re.search(r"0% \{ transform: none; \}", pop)  # starts as painted: no dip
        assert "scale(1.12)" in pop and re.search(r"100% \{ transform: none; \}", pop)
        assert "rotate" not in pop and "translate" not in pop and "opacity" not in pop
    assert "@keyframes wiggle" not in css and "wiggle" not in css  # no shake
    rules = {
        ".nav a.wake .icon": "wake",
        ".nav a:hover .icon, .nav a:focus-visible .icon": "nav-pop",
        ".nav a:active .icon": "wake",
    }
    for selector, name in rules.items():
        found = re.search(
            re.escape(selector) + r" \{ animation: ([\w-]+) ([0-9.]+)s ([^;]*); \}", css
        )
        assert found, selector
        assert found.group(1) == name and 0.35 <= float(found.group(2)) <= 0.55, selector
        assert "backwards" not in found.group(3) and "infinite" not in found.group(3)
    # press wins over hover and the page-open swell (same specificity, declared last)
    assert css.index(".nav a:active .icon") > css.index(".nav a.wake .icon")
    assert css.index(".nav a:hover .icon, ") > css.index(".nav a.wake .icon")  # current item pops
    # no glow on the menu: no filter, drop or text shadow, no box shadow, no Lottie layer
    for selector, body in re.findall(r"([^{}]*\.nav[^{}]*)\{([^{}]*)\}", css):
        for prop in ("filter", "drop-shadow", "text-shadow", "box-shadow"):
            assert prop not in body, (selector.strip(), prop)
    assert ".nav-ico .lottie-layer" not in css
    script = make_client().get("/motion.js").text
    assert not re.search(r"\bping\b", script) and "nav-ico" not in script
    assert "mouseover" not in script
    assert make_client().get("/assets/lottie/ping.json").status_code == 404
    assert not (STATIC / "lottie" / "ping.json").exists()
    assert "float" not in make_client().get("/motion.js").text.replace("overflow", "")


def test_reduced_motion_switches_off_every_restored_animation() -> None:
    css = make_client().get("/panel.css").text

    reduced = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "*, *::before, *::after" in reduced and "animation: none !important" in reduced
    assert "transition: none !important" in reduced
    # declared after every animated rule, so !important there wins over each of them
    reduced_at = css.index("@media (prefers-reduced-motion: reduce)")
    for needle in ("animation-name: rise", ".nav a.wake .icon", ".nav a:hover .icon"):
        assert css.index(needle) < reduced_at, needle


def test_stylesheet_repaints_animations_with_theme_tokens() -> None:
    css = make_client().get("/panel.css").text

    for selector in (
        ".lt-stroke path",
        ".lt-fill path",
        ".lt-gold-fill path",
    ):
        assert selector in css
    assert ".lt-gold-stroke" not in css  # no animation layer uses it
    assert "http://" not in css and "https://" not in css and "@import" not in css
    assert "@media (prefers-reduced-motion: reduce)" in css


# --- manifest ---------------------------------------------------------------------------


def test_manifest_hashes_match_every_shipped_file() -> None:
    files = manifest()["files"]

    assert files
    for relative, meta in files.items():
        data = (STATIC / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == meta["sha256"], relative


def test_every_shipped_file_is_listed_and_nothing_else_ships() -> None:
    listed = set(manifest()["files"])
    on_disk = {
        path.relative_to(STATIC).as_posix()
        for path in STATIC.rglob("*")
        if path.is_file() and path.name not in {"MANIFEST.json", "motion.js"}
    }

    assert on_disk == listed
    assert all(not path.is_symlink() for path in STATIC.rglob("*"))


def test_manifest_covers_the_lottie_library_each_animation_and_the_icons() -> None:
    files = manifest()["files"]
    served = make_client()

    lib = files["vendor/lottie_light.min.js"]["sha256"]
    assert hashlib.sha256(served.get("/assets/lottie.min.js").content).hexdigest() == lib
    for name in LOTTIE_FRAMES:
        body = served.get(f"/assets/lottie/{name}.json").content
        assert hashlib.sha256(body).hexdigest() == files[f"lottie/{name}.json"]["sha256"]
    assert any(key.startswith("icons/") for key in files)


FONT_PACKAGES = ("plex-sans", "plex-serif", "plex-mono")


def test_manifest_pins_every_package_with_version_licence_and_registry_integrity() -> None:
    sources = manifest()["sources"]

    assert set(sources) == {"phosphor", "lottie", *FONT_PACKAGES}
    for key, source in sources.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", source["version"])
        assert re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", source["integrity"])
        assert source["license"] == ("OFL-1.1" if key in FONT_PACKAGES else "MIT")
        assert source["url"].startswith("https://github.com/")
    assert sources["phosphor"]["package"] == "@phosphor-icons/core"
    assert sources["lottie"]["package"] == "lottie-web"
    # IBM's own packages (the copyright holder's web builds), not third-party repackagings
    assert sources["plex-sans"]["package"] == "@ibm/plex-sans"
    assert sources["plex-serif"]["package"] == "@ibm/plex-serif"
    assert sources["plex-mono"]["package"] == "@ibm/plex-mono"
    assert sources["lottie"]["file"].endswith("lottie_light.min.js")  # no expression engine


def test_the_vendor_tool_agrees_with_the_manifest_on_disk() -> None:
    tool = ROOT / "tools" / "assets" / "vendor.py"
    if not tool.exists():
        pytest.skip("tools/ is not part of this checkout")
    result = subprocess.run(  # noqa: S603  # fixed argv, repository file
        [sys.executable, "-I", str(tool), "check"], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert "manifest ok" in result.stdout


def test_third_party_notices_list_every_package_with_its_version_and_licence() -> None:
    notices = ROOT / "THIRD_PARTY_NOTICES.md"
    if not notices.exists():
        pytest.skip("THIRD_PARTY_NOTICES.md is not part of this checkout")
    text = notices.read_text(encoding="utf-8")
    sources = manifest()["sources"]

    for source in sources.values():
        assert source["package"] in text and source["version"] in text
        assert source["url"] in text
    assert text.count("Permission is hereby granted, free of charge") >= 2
    assert "Phosphor Icons" in text and "lottie-web" in text
    # SIL OFL 1.1, in full, once per typeface (IBM's LICENSE.txt), with the copyright line
    # each font carries
    assert text.count("SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007") == 3
    assert text.count("PERMISSION & CONDITIONS") == 3 and text.count("DISCLAIMER") >= 3
    for line in (
        "Copyright 2018 IBM Corp. All rights reserved.",
        "Copyright 2020 IBM Corp. All rights reserved.",
        "Copyright 2017 IBM Corp. All rights reserved.",
    ):
        assert line in text, line
    assert "IBM Plex Sans" in text and "IBM Plex Serif" in text and "IBM Plex Mono" in text
    assert "Instrument Sans" not in text and "Geist" not in text and "fontsource" not in text
    # the Reserved Font Name line is stated (in each section and each licence text), and the
    # files are the copyright holder's own builds, unmodified
    assert text.count('Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"') == 6
    assert text.count('a licença declara Reserved Font Name "Plex"') == 3
    assert (
        text.count(
            "Modificação: nenhuma; arquivos produzidos e publicados pelo detentor do copyright."
        )
        == 3
    )
    for family in ("Sans", "Serif", "Mono"):
        assert f'"Panel {family}" e "Panel {family} Fallback"' in text, family


# --- Phosphor icons ---------------------------------------------------------------------


@pytest.fixture
def fresh_icons() -> Iterator[None]:
    icons._vetted.cache_clear()
    yield
    icons._vetted.cache_clear()


def test_an_icon_is_a_decorative_inline_svg_that_follows_the_text_colour() -> None:
    markup = str(icons.icon("house"))

    assert markup.startswith('<svg class="icon" viewBox="0 0 256 256" fill="currentColor" ')
    assert 'aria-hidden="true"' in markup and 'focusable="false"' in markup
    assert " width=" not in markup and " height=" not in markup  # CSS sizes it
    assert markup.count("<path ") == 1 and markup.endswith("</svg>")
    assert "<script" not in markup and "style=" not in markup
    assert not re.search(r"\son\w+=", markup)


def test_weights_and_extra_classes() -> None:
    assert str(icons.icon("users", "fill", "extra")).startswith('<svg class="icon extra"')
    duotone = str(icons.icon("lock", "duotone"))
    assert duotone.count("<path ") == 2 and 'opacity="0.2"' in duotone
    assert str(icons.icon("house")) != str(icons.icon("house", "fill"))


@pytest.mark.parametrize(
    ("name", "weight"),
    [
        ("nope", "regular"),
        ("", "regular"),
        ("house", "bold"),
        ("house", "thin"),
        ("lock", "regular"),  # only duotone is vendored for it
        ("../house", "regular"),
        ("house.svg", "regular"),
        ("house-fill", "regular"),
        ("HOUSE", "regular"),
        ("house\x00", "regular"),
        ("/etc/passwd", "regular"),
    ],
)
def test_the_icon_helper_rejects_names_outside_the_vetted_set(name: str, weight: str) -> None:
    with pytest.raises(icons.UnknownIcon):
        icons.icon(name, weight)


@pytest.mark.parametrize("css_class", ['x" onload="alert(1)', "<b>", "a b;c", "1x", " lead"])
def test_the_icon_helper_rejects_unsafe_classes(css_class: str) -> None:
    with pytest.raises(icons.UnknownIcon):
        icons.icon("house", "regular", css_class)


def test_the_vetted_set_is_exactly_the_icon_files_in_the_manifest() -> None:
    names = {key for key in manifest()["files"] if key.startswith("icons/")}
    files = {f"icons/{path.name}" for path in (STATIC / "icons").iterdir()}

    assert files == names
    assert len(icons.available()) == len(names)


def test_icon_files_have_only_path_elements_and_no_dimensions() -> None:
    for path in (STATIC / "icons").iterdir():
        text = path.read_text(encoding="utf-8")
        assert re.fullmatch(
            r'<svg xmlns="http://www\.w3\.org/2000/svg" viewBox="0 0 256 256" fill="currentColor">'
            r'(?:<path d="[0-9A-Za-z.,\- ]+"(?: opacity="0\.2")?/>)+</svg>\n?',
            text,
        ), path.name


def _copy_static(tmp_path: Path) -> Path:
    target = tmp_path / "static"
    shutil.copytree(STATIC, target)
    return target


def test_a_tampered_icon_is_refused_even_if_the_manifest_is_updated_to_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_icons: None
) -> None:
    target = _copy_static(tmp_path)
    monkeypatch.setattr(icons, "STATIC_DIR", target)
    victim = target / "icons" / "house.svg"

    # 1) bytes changed, manifest not: refused by the hash.
    victim.write_text(victim.read_text().replace("</svg>", "<script>1</script></svg>"))
    with pytest.raises(assets.AssetIntegrityError, match="manifest"):
        icons._vetted()

    # 2) manifest rewritten to bless the new bytes: refused by the structure check.
    data = json.loads((target / "MANIFEST.json").read_text())
    data["files"]["icons/house.svg"]["sha256"] = hashlib.sha256(victim.read_bytes()).hexdigest()
    (target / "MANIFEST.json").write_text(json.dumps(data))
    icons._vetted.cache_clear()
    with pytest.raises(assets.AssetIntegrityError, match="plain path-only SVG"):
        icons._vetted()


def test_a_file_that_is_not_in_the_manifest_is_never_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_icons: None
) -> None:
    target = _copy_static(tmp_path)
    (target / "icons" / "sneaky.svg").write_text(
        (target / "icons" / "house.svg").read_text(), encoding="utf-8"
    )
    monkeypatch.setattr(icons, "STATIC_DIR", target)

    with pytest.raises(icons.UnknownIcon):
        icons.icon("sneaky")


def test_a_tampered_asset_stops_the_panel_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _copy_static(tmp_path)
    (target / "lottie" / "check.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(assets, "STATIC_DIR", target)

    with pytest.raises(assets.AssetIntegrityError):
        make_client()


def test_a_tampered_icon_stops_the_panel_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_icons: None
) -> None:
    target = _copy_static(tmp_path)
    victim = target / "icons" / "house.svg"
    victim.write_text(victim.read_text().replace('d="', 'd="M0 0 '), encoding="utf-8")
    monkeypatch.setattr(assets, "STATIC_DIR", target)
    monkeypatch.setattr(icons, "STATIC_DIR", target)

    with pytest.raises(assets.AssetIntegrityError, match=r"icons/house\.svg"):
        make_client()  # at startup, not at the first page render


def test_a_missing_icon_stops_the_panel_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_icons: None
) -> None:
    target = _copy_static(tmp_path)
    (target / "icons" / "house.svg").unlink()
    monkeypatch.setattr(assets, "STATIC_DIR", target)
    monkeypatch.setattr(icons, "STATIC_DIR", target)

    with pytest.raises(assets.AssetIntegrityError, match=r"icons/house\.svg"):
        make_client()


def test_startup_verifies_the_icons_from_disk_not_from_an_earlier_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_icons: None
) -> None:
    icons.available()  # a previous app in the same process filled the cache
    target = _copy_static(tmp_path)
    victim = target / "icons" / "house.svg"
    victim.write_text(victim.read_text().replace('d="', 'd="M0 0 '), encoding="utf-8")
    monkeypatch.setattr(assets, "STATIC_DIR", target)
    monkeypatch.setattr(icons, "STATIC_DIR", target)

    with pytest.raises(assets.AssetIntegrityError):
        make_client()


def test_a_file_missing_from_the_manifest_is_an_integrity_error_not_a_key_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _copy_static(tmp_path)
    data = json.loads((target / "MANIFEST.json").read_text())
    del data["files"]["lottie/check.json"]
    (target / "MANIFEST.json").write_text(json.dumps(data))
    monkeypatch.setattr(assets, "STATIC_DIR", target)

    with pytest.raises(
        assets.AssetIntegrityError, match=r"lottie/check\.json is not in the manifest"
    ):
        assets.load()


def _template_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(TEMPLATES.glob("*.html")))


def test_every_literal_icon_call_in_the_templates_exists() -> None:
    available = icons.available()
    calls = re.findall(r'icon\(\s*"([^"]+)"(?:\s*,\s*"([^"]+)")?', _template_text())

    assert calls
    for name, weight in calls:
        assert (name, weight or "regular") in available, (name, weight)


def test_nav_glyphs_exist_in_regular_and_fill_and_alerts_in_duotone() -> None:
    available = icons.available()
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    nav = re.search(r"\{% set nav = \[(.*?)\] -%\}", base, re.S)
    assert nav
    glyphs = re.findall(r'\("[a-z]+", "/[a-z]*", "[^"]+", "([a-z-]+)"\)', nav.group(1))
    assert len(glyphs) == 7
    for glyph in glyphs:
        assert (glyph, "regular") in available and (glyph, "fill") in available
    for name in ("lock", "warning", "check-circle", "info", "x-circle"):  # alerts: functional
        assert (name, "duotone") in available, name
    # purely decorative icons are gone: empty states, headings and cards carry none
    for name in ("tray", "folder-open", "question"):
        assert not any(n == name for n, _w in available), name
    assert ("buildings", "duotone") not in available and ("users", "duotone") not in available


def test_no_vendored_icon_is_unused() -> None:
    text = _template_text()
    names = {name for name, _weight in icons.available()}

    unused = {name for name in names if f'"{name}"' not in text}
    assert not unused, unused


def test_templates_cannot_inject_markup_except_through_the_icon_helper() -> None:
    for template in TEMPLATES.glob("*.html"):
        source = template.read_text(encoding="utf-8")
        assert not re.search(r"\|\s*safe\b", source), template.name
        assert "{% autoescape" not in source and "Markup(" not in source, template.name
    from osa.panel.routes.common import templates

    assert templates.env.autoescape  # Jinja's escaping stays on
    assert templates.env.globals["icon"] is icons.icon


BRAND_SVG = (
    '<svg viewBox="0 0 32 32" aria-hidden="true" focusable="false">\n'
    '      <path class="shield-fill shield-line" d="M16 2.5 5 6.5v8.2c0 6.6 4.5 11.6 11 14.8 '
    '6.5-3.2 11-8.2 11-14.8V6.5L16 2.5Z" stroke-width="1"/>\n'
    '      <path class="shield-cross" d="M16 8v13M11.5 12.5h9" stroke-width="1.6" '
    'stroke-linecap="round" fill="none"/>\n'
    '      <path class="shield-drop" d="M16 24.2 13.6 21h4.8L16 24.2Z"/>\n'
    "    </svg>"
)


def test_the_manual_page_inlines_only_vetted_svg_and_the_fixed_brand_mark() -> None:
    page = make_client().get("/manual").text

    svgs = re.findall(r"<svg\b.*?</svg>", page, re.S)
    assert svgs.count(BRAND_SVG) == 1  # the hand-drawn mark, a template literal, once
    for svg in svgs:
        if svg == BRAND_SVG:
            continue
        assert re.fullmatch(
            r'<svg class="icon[a-z -]*" viewBox="0 0 256 256" fill="currentColor" '
            r'aria-hidden="true" focusable="false">(?:<path d="[0-9A-Za-z.,\- ]+"'
            r'(?: opacity="0\.2")?/>)+</svg>',
            svg,
        ), svg[:120]


def test_the_brand_mark_is_not_a_phosphor_icon_and_has_no_animation_hook() -> None:
    page = make_client().get("/manual").text

    assert "breathe" not in page and "brand-mark" not in page
    brand = re.search(r'<a class="brand" href="/">(.*?)</a>', page, re.S)
    assert brand and "data-lottie" not in brand.group(1) and "lottie" not in brand.group(1)
    assert ("shield-check", "fill") not in icons.available()  # nothing Phosphor for the mark
    assert not (STATIC / "lottie" / "breathe.json").exists()
    assert "breathe" not in make_client().get("/motion.js").text


def test_the_brand_pops_softly_once_and_never_loops() -> None:
    css = make_client().get("/panel.css").text

    # Without the script: a CSS pop on hover, focus and press (only while it is not .live).
    assert ".brand:not(.live):hover svg, .brand:not(.live):focus-visible svg" in css
    assert re.search(
        r"\.brand:not\(\.live\):hover svg[^{]*\{ animation: pop 0\.52s linear; \}", css
    )
    assert re.search(
        r"\.brand:not\(\.live\):active svg \{ animation: pop-tap 0\.42s linear; \}", css
    )
    for name, stops in (("pop", 4), ("pop-tap", 3)):
        body = re.search(rf"@keyframes {name} \{{(.*?)\n\}}", css, re.S)
        assert body, name
        assert len(re.findall(r"^\s*\d+% \{", body.group(1), re.M)) >= stops + 1  # spring stops
        assert "animation-timing-function" in body.group(1)  # eased, no abrupt step
    # Durations: full pop 450-650 ms, press pop 350-500 ms; nothing infinite anywhere.
    rules = re.findall(r"([^{}]*\.brand[^{}]*)\{([^{}]*)\}", css)
    assert rules
    for selector, body in rules:
        assert "infinite" not in body, selector
        for name, seconds in re.findall(r"animation:\s*(pop(?:-tap)?)\s+([0-9.]+)s", body):
            low, high = (0.45, 0.65) if name == "pop" else (0.35, 0.5)
            assert low <= float(seconds) <= high, (selector, seconds)
    assert "infinite" not in css  # no CSS loop anywhere in the panel
    # With the script the transform comes from custom properties, and the mark is promoted
    # to its own layer only while the interaction lasts.
    base = re.search(r"\n\.brand svg \{(.*?)\n\}", css, re.S)
    assert base
    for prop in ("--mx", "--my", "--tilt", "--lift"):
        assert prop in base.group(1)
    assert "will-change" not in base.group(1)
    assert ".brand.brand-active svg { will-change: transform; }" in css
    assert css.count("will-change") == 1
    reduced = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "animation: none !important" in reduced and "transition: none !important" in reduced


def test_favicon_is_the_hand_drawn_mark_without_script_or_remote_reference() -> None:
    svg = make_client().get("/favicon.svg").text

    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">')
    assert "M16 2.5 5 6.5v8.2c0 6.6 4.5 11.6 11 14.8" in svg  # same outline as the header mark
    for forbidden in ("<script", "<style", "href", "onload", "<image", "<foreignObject", "url("):
        assert forbidden not in svg
    assert manifest()["files"]["favicon.svg"]["origin"] == "own"


# --- Lottie files ----------------------------------------------------------------------


def _walk(node: Any) -> Iterator[Any]:
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


@pytest.mark.parametrize("name", sorted(LOTTIE_FRAMES))
def test_lottie_file_is_small_valid_and_expression_free(name: str) -> None:
    raw = (STATIC / "lottie" / f"{name}.json").read_bytes()
    doc = json.loads(raw)

    assert len(raw) < 10_000  # well under the ~10 KB budget
    assert {"v", "fr", "ip", "op", "w", "h", "layers", "assets"} <= set(doc)
    assert doc["fr"] == 30 and doc["w"] == doc["h"] == 64 and doc["ip"] == 0
    assert doc["op"] == LOTTIE_FRAMES[name]
    assert doc["assets"] == [] and doc["layers"]
    for layer in doc["layers"]:
        assert layer["ty"] == 4  # shape layers only: no image, text, precomp, null, solid
        assert 0 <= layer["ip"] < layer["op"] <= doc["op"]
        assert layer["cl"] in {"lt-stroke", "lt-fill", "lt-gold-fill"}
        assert "ef" not in layer and "masksProperties" not in layer
    for node in _walk(doc):
        if isinstance(node, dict):
            assert not isinstance(node.get("x"), str), "expression found"  # easing x is numeric
            assert "ef" not in node and "tm" not in node  # no effects, no time remapping
            assert node.get("ty") not in {"gf", "gs", "mm", "rp", "rd"}  # no gradients/modifiers
    keyframes = [
        key["t"]
        for node in _walk(doc)
        if isinstance(node, dict) and node.get("a") == 1
        for key in node["k"]
    ]
    assert keyframes and all(doc["ip"] <= t <= doc["op"] for t in keyframes)
    assert "http" not in raw.decode("utf-8")


@pytest.mark.parametrize("name", LOOPS)
def test_loops_end_where_they_start(name: str) -> None:
    doc = json.loads((STATIC / "lottie" / f"{name}.json").read_text(encoding="utf-8"))

    checked = 0
    for node in _walk(doc):
        if isinstance(node, dict) and node.get("a") == 1 and isinstance(node["k"], list):
            first, last = node["k"][0]["s"], node["k"][-1]["s"]
            assert len(first) == len(last)
            for a, b in zip(first, last, strict=True):
                assert (a - b) % 360 == 0, (name, first, last)  # full turns count as equal
            checked += 1
    assert checked


def _generator() -> Any:
    path = ROOT / "tools" / "lottie" / "generate.py"
    if not path.exists():
        pytest.skip("tools/ is not part of this checkout")
    spec = importlib.util.spec_from_file_location("osa_lottie_generate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", sorted(LOTTIE_FRAMES))
def test_each_animation_is_exactly_what_the_generator_writes(name: str) -> None:
    module = _generator()
    first = module.render(module.ANIMATIONS[name]())
    second = module.render(module.ANIMATIONS[name]())

    assert first == second  # deterministic
    assert (STATIC / "lottie" / f"{name}.json").read_text(encoding="utf-8") == first


def test_the_theme_animation_has_the_two_resting_frames_the_script_expects() -> None:
    doc = json.loads((STATIC / "lottie" / "theme.json").read_text(encoding="utf-8"))
    times = {
        key["t"]
        for node in _walk(doc)
        if isinstance(node, dict) and node.get("a") == 1
        for key in node["k"]
    }

    assert {0, 24, 48} <= times  # sun, moon, sun again (THEME_FRAMES in motion.js)
    assert max(times) < doc["op"]
