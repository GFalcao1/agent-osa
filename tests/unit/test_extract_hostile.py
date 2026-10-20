"""Hostile and pathological inputs: every one must end as a typed error, quickly."""

from __future__ import annotations

import base64
import io
import os
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from osa.domain.document import FileClass
from osa.domain.errors import ExtractionFailed, SensitiveFile, Unsupported
from osa.extract import ExtractLimits, extract
from osa.extract import isolate as isolate_module
from osa.extract import pdf as pdf_module
from osa.extract.image import load_image
from osa.extract.isolate import _decode, run_worker

PYTHON = sys.executable

BILLION_LAUGHS = (
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
    '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
    '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
    '<!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">'
    "]><lolz>&lol4;</lolz>"
)
EXTERNAL_ENTITY = (
    '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/hostname">]>'
    "<foo>&xxe;</foo>"
)
DTD_ONLY = '<?xml version="1.0"?><!DOCTYPE foo SYSTEM "http://example.invalid/x.dtd"><foo/>'


def rewrite_zip(source: Path, target: Path, part: str, new_xml: str) -> Path:
    """Copy ``source`` replacing one part by hostile XML."""
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        assert part in src.namelist(), part
        for info in src.infolist():
            data = src.read(info.filename)
            out.writestr(info.filename, new_xml.encode() if info.filename == part else data)
    return target


@pytest.mark.parametrize("payload", [BILLION_LAUGHS, EXTERNAL_ENTITY, DTD_ONLY])
@pytest.mark.parametrize(
    ("fixture", "part"),
    [
        ("contrato.docx", "word/document.xml"),
        ("planilha.xlsx", "xl/workbook.xml"),
        ("planilha.xlsx", "xl/worksheets/sheet1.xml"),
        ("planilha.xlsx", "docProps/core.xml"),  # a part the readers never open
    ],
)
def test_xml_with_dtd_or_entities_is_refused(
    tmp_path: Path, docs: dict[str, Path], payload: str, fixture: str, part: str
) -> None:
    hostile = rewrite_zip(docs[fixture], tmp_path / fixture, part, payload)

    started = time.monotonic()
    with pytest.raises(ExtractionFailed):
        extract(hostile)

    assert time.monotonic() - started < 20


def test_zip_with_too_many_entries_is_failed_not_parsed(
    tmp_path: Path, docs: dict[str, Path]
) -> None:
    many = tmp_path / "muitas.xlsx"
    with zipfile.ZipFile(docs["planilha.xlsx"]) as src, zipfile.ZipFile(many, "w") as out:
        for info in src.infolist():
            out.writestr(info.filename, src.read(info.filename))
        for index in range(60):
            out.writestr(f"extra/{index}.txt", b"x")

    with pytest.raises(ExtractionFailed, match="entradas"):
        extract(many, ExtractLimits(max_zip_entries=50))
    assert extract(many).method == "xlsx"  # the default limit would have accepted it


def test_declared_total_size_over_the_limit_is_refused(
    tmp_path: Path, docs: dict[str, Path]
) -> None:
    bomb = tmp_path / "bomba.docx"
    with zipfile.ZipFile(docs["contrato.docx"]) as src, zipfile.ZipFile(bomb, "w") as out:
        for info in src.infolist():
            out.writestr(info.filename, src.read(info.filename), zipfile.ZIP_DEFLATED)
        out.writestr("word/media/zeros.bin", bytes(3_000_000), zipfile.ZIP_DEFLATED)

    with pytest.raises(ExtractionFailed, match="grande"):
        extract(bomb, ExtractLimits(max_unzipped_bytes=2_000_000, max_part_bytes=1_000_000))
    assert bomb.stat().st_size < 200_000  # tiny on disk, big when expanded


def test_a_part_over_the_read_limit_is_cut_off_while_reading(
    tmp_path: Path, docs: dict[str, Path]
) -> None:
    # The declared sizes are honest and under the total limit; only the per-part
    # cap enforced during the read can stop this one.
    bomb = tmp_path / "parte.docx"
    with zipfile.ZipFile(docs["contrato.docx"]) as src, zipfile.ZipFile(bomb, "w") as out:
        for info in src.infolist():
            out.writestr(info.filename, src.read(info.filename), zipfile.ZIP_DEFLATED)
        out.writestr("word/media/zeros.bin", bytes(3_000_000), zipfile.ZIP_DEFLATED)

    with pytest.raises(ExtractionFailed, match="parte"):
        extract(bomb, ExtractLimits(max_part_bytes=1_000_000))


def test_forged_declared_size_does_not_bypass_the_limits(
    tmp_path: Path, docs: dict[str, Path]
) -> None:
    forged = tmp_path / "forjado.docx"
    with zipfile.ZipFile(docs["contrato.docx"]) as src, zipfile.ZipFile(forged, "w") as out:
        for info in src.infolist():
            out.writestr(info.filename, src.read(info.filename), zipfile.ZIP_DEFLATED)
        out.writestr("word/media/zeros.bin", bytes(3_000_000), zipfile.ZIP_DEFLATED)
    raw = bytearray(forged.read_bytes())
    # Lie about the uncompressed size of the last entry in local and central headers.
    for signature, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        index = raw.rfind(signature)
        raw[index + offset : index + offset + 4] = struct.pack("<I", 100)
    forged.write_bytes(bytes(raw))

    with pytest.raises(ExtractionFailed):
        extract(forged, ExtractLimits(max_part_bytes=1_000_000, max_unzipped_bytes=2_000_000))


def test_file_size_limit(tmp_path: Path, docs: dict[str, Path]) -> None:
    with pytest.raises(ExtractionFailed, match="grande"):
        extract(docs["texto.pdf"], ExtractLimits(max_file_bytes=500))


def test_image_with_too_many_pixels_is_refused_from_the_header(tmp_path: Path) -> None:
    flat = tmp_path / "enorme.png"
    Image.new("L", (6000, 6000), 255).save(flat, optimize=True)
    assert flat.stat().st_size < 200_000

    with pytest.raises(ExtractionFailed, match="grande"):
        extract(flat, ExtractLimits(max_pixels=10_000_000))


def test_decompression_bomb_warning_is_an_error_and_pillow_state_is_restored(
    tmp_path: Path,
) -> None:
    flat = tmp_path / "grande.png"
    Image.new("L", (3000, 3000), 255).save(flat)
    before = Image.MAX_IMAGE_PIXELS

    with pytest.raises(ExtractionFailed):
        load_image(flat, ExtractLimits(max_pixels=1_000_000))

    assert Image.MAX_IMAGE_PIXELS == before


def test_pdf_page_too_large_is_refused_before_it_is_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    huge = tmp_path / "gigante.pdf"
    pdf = canvas.Canvas(str(huge), pagesize=(14400, 14400))
    pdf.showPage()
    pdf.save()

    def render_must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("the page was rendered")

    monkeypatch.setattr(pdf_module, "_render", render_must_not_run)

    with pytest.raises(ExtractionFailed, match="grande"):
        pdf_module.extract_pdf(huge, ExtractLimits(max_render_pixels=1_000_000))


def test_pdf_page_raster_is_lowered_to_fit_the_pixel_cap(tmp_path: Path) -> None:
    large = tmp_path / "grande.pdf"
    pdf = canvas.Canvas(str(large), pagesize=(2000, 2000))
    pdf.showPage()
    pdf.save()

    content = extract(large, ExtractLimits(max_render_pixels=1_000_000, max_image_side=5000))

    # 2000 pt at 200 dpi would be 5555 px a side; the cap keeps it near 1000.
    assert content.images[0].width * content.images[0].height <= 1_050_000


# --- isolation -------------------------------------------------------------


def fast_limits(**overrides: Any) -> ExtractLimits:
    return ExtractLimits(timeout_s=overrides.pop("timeout_s", 20.0), **overrides)


def test_timeout_kills_the_worker_and_its_process_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    script = f"import os, time;open({str(pid_file)!r}, 'w').write(str(os.getpid()));time.sleep(60)"
    started = time.monotonic()

    with pytest.raises(ExtractionFailed, match="tempo limite"):
        run_worker(tmp_path / "x.pdf", fast_limits(timeout_s=1.5), argv=[PYTHON, "-c", script])

    assert time.monotonic() - started < 15
    pid = int(pid_file.read_text())
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the worker survived the timeout")


def test_memory_limit_kills_the_worker(tmp_path: Path) -> None:
    script = "data = bytearray(b'x') * (900 * 1024 * 1024); import time; time.sleep(60)"
    started = time.monotonic()

    with pytest.raises(ExtractionFailed, match="memória"):
        run_worker(tmp_path / "x.pdf", fast_limits(max_memory_mb=300), argv=[PYTHON, "-c", script])

    assert time.monotonic() - started < 30


def test_worker_crash_is_a_typed_error(tmp_path: Path) -> None:
    with pytest.raises(ExtractionFailed, match="anormal"):
        run_worker(tmp_path / "x.pdf", fast_limits(), argv=[PYTHON, "-c", "import os; os._exit(3)"])
    with pytest.raises(ExtractionFailed, match="anormal"):
        run_worker(tmp_path / "x.pdf", fast_limits(), argv=[PYTHON, "-c", "print('not json')"])
    with pytest.raises(ExtractionFailed):
        run_worker(tmp_path / "x.pdf", fast_limits(), argv=["/nonexistent/worker-binary"])


def test_worker_gets_a_minimal_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_TELEGRAM_BOT_TOKEN", "fictional-token")
    monkeypatch.setenv("OSA_EXTERNAL_LLM_API_KEY", "fictional-key")
    script = (
        "import json, os;"
        "names = ','.join(sorted(os.environ));"
        "print(json.dumps({'ok': False, 'kind': 'failed', 'message': names}))"
    )

    with pytest.raises(ExtractionFailed) as info:
        run_worker(tmp_path / "x.pdf", fast_limits(), argv=[PYTHON, "-c", script])

    assert "OSA_" not in str(info.value)
    assert "fictional" not in str(info.value)


def test_worker_cannot_open_network_connections() -> None:
    script = (
        "from osa.extract.worker import _disable_network; _disable_network();"
        "import socket;"
        "\ntry:\n socket.create_connection(('127.0.0.1', 9), timeout=1)\n"
        "except OSError as e:\n print('blocked:', e)\n"
        "else:\n print('connected')"
    )

    done = subprocess.run([PYTHON, "-I", "-c", script], capture_output=True, text=True, timeout=30)

    assert "blocked: network access is disabled" in done.stdout


def test_worker_ignores_python_environment_overrides(
    tmp_path: Path, docs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "json.py").write_text("raise SystemExit('shadowed json module was imported')\n")
    monkeypatch.setenv("PYTHONPATH", str(shadow))

    assert extract(docs["contrato.docx"]).method == "docx"


# --- the worker's answer is data, validated ---------------------------------

OK_CONTENT = {
    "text": "ola",
    "images": [],
    "pages_total": 1,
    "truncated": False,
    "method": "docx",
}
PNG_B64 = base64.b64encode(bytes.fromhex("89504e470d0a1a0a") + b"rest").decode()


def decode(content: dict[str, Any], limits: ExtractLimits | None = None) -> Any:
    return _decode({"ok": True, "content": content}, limits or ExtractLimits())


def test_valid_worker_answer_is_decoded() -> None:
    image = {"data": PNG_B64, "page": 1, "width": 2, "height": 3}

    content = decode({**OK_CONTENT, "images": [image]})

    assert content.text == "ola"
    assert content.images[0].width == 2


@pytest.mark.parametrize(
    "mutation",
    [
        {"method": "pickle"},
        {"text": 5},
        {"text": "x" * 20_000},
        {"pages_total": 0},
        {"pages_total": True},
        {"truncated": "no"},
        {"images": "nope"},
        {"images": [{"data": "not base64!!", "page": 1, "width": 1, "height": 1}]},
        {
            "images": [
                {"data": base64.b64encode(b"GIF89a").decode(), "page": 1, "width": 1, "height": 1}
            ]
        },
        {"images": [{"data": PNG_B64, "page": 0, "width": 1, "height": 1}]},
        {"images": [{"data": PNG_B64, "page": 1, "width": "1", "height": 1}]},
        {"images": [{"data": PNG_B64, "page": 1, "width": 1, "height": 1}] * 6},
    ],
)
def test_malformed_worker_answers_are_rejected(mutation: dict[str, Any]) -> None:
    with pytest.raises(ExtractionFailed):
        decode({**OK_CONTENT, **mutation})


def test_worker_answer_that_is_not_an_object_or_lacks_fields_is_rejected() -> None:
    for payload in ([], "ok", None, {"ok": True}, {"ok": True, "content": {}}):
        with pytest.raises(ExtractionFailed):
            _decode(payload, ExtractLimits())


def test_worker_reported_kinds_map_to_the_typed_errors() -> None:
    limits = ExtractLimits()
    with pytest.raises(Unsupported):
        _decode({"ok": False, "kind": "unsupported", "message": "x"}, limits)
    with pytest.raises(SensitiveFile):
        _decode({"ok": False, "kind": "sensitive", "message": "x"}, limits)
    with pytest.raises(ExtractionFailed):
        _decode({"ok": False, "kind": "failed", "message": "x"}, limits)
    with pytest.raises(ExtractionFailed):
        _decode({"ok": False}, limits)


def test_group_rss_reports_this_process_group() -> None:
    assert isolate_module.group_rss_mb(os.getpgrp()) > 1
    assert isolate_module.group_rss_mb(2**22 + 17) == 0.0


def test_hostile_pixel_count_in_a_real_image_through_the_isolated_worker(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.new("1", (20000, 20000), 1).save(buffer, format="PNG", optimize=True)
    flat = tmp_path / "plana.png"
    flat.write_bytes(buffer.getvalue())

    with pytest.raises(ExtractionFailed):
        extract(flat)


# --- review fixes ------------------------------------------------------------


def forged_entry_count_docx(tmp_path: Path, real_entries: int = 300) -> Path:
    """A zip whose end record declares 3 entries while the directory holds many more."""
    forged = tmp_path / "contagem_forjada.docx"
    with zipfile.ZipFile(forged, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
        for index in range(real_entries):
            archive.writestr(f"x/{index}", b"a")
    raw = bytearray(forged.read_bytes())
    eocd = raw.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", raw, eocd + 8, 3, 3)
    forged.write_bytes(bytes(raw))
    return forged


def test_forged_entry_count_in_the_end_record_does_not_bypass_the_limit(tmp_path: Path) -> None:
    from osa.extract import zipguard
    from osa.extract.classify import file_class

    forged = forged_entry_count_docx(tmp_path)
    limits = ExtractLimits(max_zip_entries=100)
    with forged.open("rb") as handle:
        assert zipguard.declared_entries(handle, forged.stat().st_size) == 3

    with pytest.raises(ExtractionFailed, match="entradas"):
        file_class(forged, limits)
    with pytest.raises(ExtractionFailed, match="entradas"):
        zipguard.open_checked(forged, limits)
    with pytest.raises(ExtractionFailed, match="entradas"):
        extract(forged, limits)


def test_central_directory_larger_than_the_entry_limit_allows_is_refused_unparsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from osa.extract import classify as classify_module
    from osa.extract.classify import file_class

    forged = forged_entry_count_docx(tmp_path, real_entries=2000)

    def must_not_parse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the central directory was parsed")

    monkeypatch.setattr(classify_module.zipfile, "ZipFile", must_not_parse)

    with pytest.raises(ExtractionFailed, match="entradas"):
        file_class(forged, ExtractLimits(max_zip_entries=10))


def test_sniff_checks_the_size_before_parsing_anything(
    docs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from osa.extract import classify as classify_module
    from osa.extract.classify import file_class

    def must_not_parse(*args: object, **kwargs: object) -> None:
        raise AssertionError("an oversized file was parsed")

    monkeypatch.setattr(classify_module.zipfile, "ZipFile", must_not_parse)
    monkeypatch.setattr(classify_module, "check_entry_count", must_not_parse)

    for name in ("contrato.docx", "texto.pdf", "imagem.png"):
        with pytest.raises(ExtractionFailed, match="grande"):
            file_class(docs[name], ExtractLimits(max_file_bytes=100))


def test_worker_checks_the_size_before_classifying(
    docs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from osa.extract import worker

    def must_not_sniff(*args: object, **kwargs: object) -> None:
        raise AssertionError("file_class ran before the size check")

    monkeypatch.setattr(worker, "file_class", must_not_sniff)

    with pytest.raises(ExtractionFailed, match="grande"):
        worker.extract_in_process(docs["contrato.docx"], ExtractLimits(max_file_bytes=100))
    with pytest.raises(SensitiveFile):
        worker.extract_in_process(docs["certificado.pfx"], ExtractLimits())


def test_cpu_time_limit_is_reported_as_such(tmp_path: Path) -> None:
    script = (
        "import resource;"
        "resource.setrlimit(resource.RLIMIT_CPU, (1, resource.getrlimit(resource.RLIMIT_CPU)[1]))\n"
        "while True: pass"
    )
    started = time.monotonic()

    with pytest.raises(ExtractionFailed, match="tempo de CPU excedido"):
        run_worker(tmp_path / "x.pdf", fast_limits(), argv=[PYTHON, "-c", script])

    assert time.monotonic() - started < 15


def test_worker_cpu_limit_leaves_room_for_the_ocr_threads() -> None:
    from osa.extract.ocr import OCR_THREADS
    from osa.extract.worker import cpu_seconds

    assert cpu_seconds(ExtractLimits(timeout_s=120)) >= 120 * OCR_THREADS


def test_memory_watchdog_that_cannot_sample_fails_the_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "vazio"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))  # no ps to run
    assert isolate_module.group_rss_mb(os.getpgrp()) is None
    script = "import time; time.sleep(30)"
    started = time.monotonic()

    with pytest.raises(ExtractionFailed, match="vigia de memória indisponível"):
        run_worker(tmp_path / "x.pdf", fast_limits(), argv=[PYTHON, "-c", script])

    assert time.monotonic() - started < 10


def test_worker_gets_a_private_temporary_directory_removed_afterwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent_tmp = tmp_path / "pai"
    parent_tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(parent_tmp))  # what $TMPDIR resolves to
    script = (
        "import json, os, pathlib;"
        "tmp = os.environ['TMPDIR'];"
        "pathlib.Path(tmp, '.ses').write_text('x');"
        "print(json.dumps({'ok': False, 'kind': 'failed', 'message': tmp}))"
    )

    with pytest.raises(ExtractionFailed) as info:
        run_worker(tmp_path / "x.pdf", fast_limits(), argv=[PYTHON, "-c", script])

    child_tmp = Path(str(info.value))
    assert child_tmp.parent == parent_tmp
    assert not child_tmp.exists()
    assert list(parent_tmp.iterdir()) == []


def test_worker_environment_has_no_python_variables(tmp_path: Path) -> None:
    script = (
        "import json, os;"
        "print(json.dumps({'ok': False, 'kind': 'failed', 'message': ','.join(os.environ)}))"
    )

    with pytest.raises(ExtractionFailed) as info:
        run_worker(tmp_path / "x.pdf", fast_limits(), argv=[PYTHON, "-c", script])

    assert "PYTHON" not in str(info.value)
    assert isolate_module.worker_argv()[1:3] == ["-I", "-B"]


def test_worker_output_over_the_cap_is_cut_off_while_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(isolate_module, "_MAX_OUTPUT", 1024 * 1024)
    script = (
        "import sys, time;"
        "sys.stdout.write('x' * (8 * 1024 * 1024)); sys.stdout.flush(); time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(ExtractionFailed, match="grande demais"):
        run_worker(tmp_path / "x.pdf", fast_limits(), argv=[PYTHON, "-c", script])

    assert time.monotonic() - started < 10


def test_worker_stderr_is_not_kept_in_memory(tmp_path: Path) -> None:
    script = (
        "import json, sys;"
        "sys.stderr.write('ruído ' * 4_000_000);"
        "print(json.dumps({'ok': False, 'kind': 'failed', 'message': 'fim'}))"
    )

    with pytest.raises(ExtractionFailed, match="fim"):
        run_worker(tmp_path / "x.pdf", fast_limits(), argv=[PYTHON, "-c", script])


def test_group_is_not_signalled_after_the_worker_was_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signalled: list[int] = []
    monkeypatch.setattr(isolate_module.os, "killpg", lambda pgid, sig: signalled.append(pgid))

    class Reaped:
        pid = 2**22 + 17

        def poll(self) -> int:
            return 0

        def kill(self) -> None:
            raise AssertionError("kill on a reaped process")

    isolate_module._kill_group(Reaped())  # type: ignore[arg-type]

    assert signalled == []


def test_timeout_also_kills_a_grandchild_in_the_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "neto"
    script = (
        "import subprocess, sys, time;"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid));"
        "time.sleep(60)"
    )

    with pytest.raises(ExtractionFailed, match="tempo limite"):
        run_worker(tmp_path / "x.pdf", fast_limits(timeout_s=2.0), argv=[PYTHON, "-c", script])

    grandchild = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(grandchild, 9)
        pytest.fail("the grandchild survived the timeout")


def test_worker_main_disables_the_network_before_extracting(tmp_path: Path) -> None:
    script = (
        "import socket\n"
        "from osa.domain.errors import ExtractionFailed\n"
        "from osa.extract import worker\n"
        "def probe(path, limits):\n"
        "    try:\n"
        "        socket.create_connection(('127.0.0.1', 9), timeout=1)\n"
        "    except OSError as exc:\n"
        "        raise ExtractionFailed(str(exc)) from exc\n"
        "    raise ExtractionFailed('rede aberta')\n"
        "worker.extract_in_process = probe\n"
        "raise SystemExit(worker.main())\n"
    )

    with pytest.raises(ExtractionFailed, match="network access is disabled"):
        run_worker(tmp_path / "x.pdf", fast_limits(), argv=[PYTHON, "-I", "-c", script])


def test_memory_error_reaches_the_dedicated_branch(
    tmp_path: Path, docs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from osa.extract import image as image_module
    from osa.extract import ocr as ocr_module

    def out_of_memory(*args: object, **kwargs: object) -> None:
        raise MemoryError

    with monkeypatch.context() as patch:
        patch.setattr(pdf_module, "_render", out_of_memory)
        with pytest.raises(MemoryError):
            pdf_module.extract_pdf(docs["escaneado.pdf"], ExtractLimits())
    with monkeypatch.context() as patch:
        patch.setattr(image_module.Image, "open", out_of_memory)
        with pytest.raises(MemoryError):
            load_image(docs["imagem.png"], ExtractLimits())
    with monkeypatch.context() as patch:
        patch.setattr(ocr_module, "_get_engine", lambda max_side: out_of_memory)
        with pytest.raises(MemoryError):
            ocr_module.ocr_image(Image.new("RGB", (8, 8)), 2000)


# --- second review round ---------------------------------------------------


def contradictory_zip64_docx(tmp_path: Path, real_entries: int = 300) -> Path:
    """Classic end record: 3 entries in 46 bytes; the ZIP64 record zipfile uses: all of them."""
    path = tmp_path / "zip64_contraditorio.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
        for index in range(real_entries):
            archive.writestr(f"x/{index}", b"a")
    raw = path.read_bytes()
    end = raw.rfind(b"PK\x05\x06")
    _sig, _d, _dd, count, total, size_cd, offset_cd, _clen = struct.unpack(
        "<4s4H2LH", raw[end : end + 22]
    )
    record = struct.pack(
        "<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, total, size_cd, offset_cd
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, end, 1)
    classic = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 3, 3, 46, offset_cd, 0)
    path.write_bytes(raw[:end] + record + locator + classic)
    with zipfile.ZipFile(path) as check:  # the parser really sees every entry
        assert len(check.infolist()) == real_entries + 1
    return path


def forbid_zip_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_parse(*args: object, **kwargs: object) -> None:
        # pytest.fail raises a BaseException, which no ``except Exception`` can swallow.
        pytest.fail("ZipFile was built for a zip that the pre-filter should have refused")

    monkeypatch.setattr(zipfile, "ZipFile", must_not_parse)


def test_zip64_end_record_is_checked_before_the_parser_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from osa.extract import zipguard
    from osa.extract.classify import file_class

    forged = contradictory_zip64_docx(tmp_path)
    limits = ExtractLimits(max_zip_entries=10)
    forbid_zip_parsing(monkeypatch)

    with pytest.raises(ExtractionFailed, match="entradas"):
        file_class(forged, limits)
    with pytest.raises(ExtractionFailed, match="entradas"):
        zipguard.open_checked(forged, limits)


def test_zip64_locator_without_a_valid_record_is_refused_unparsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from osa.extract import zipguard
    from osa.extract.classify import file_class

    forged = contradictory_zip64_docx(tmp_path)
    raw = bytearray(forged.read_bytes())
    record = raw.rfind(b"PK\x06\x06")
    raw[record : record + 4] = b"XXXX"  # the locator now points at garbage
    forged.write_bytes(bytes(raw))
    forbid_zip_parsing(monkeypatch)

    with pytest.raises(ExtractionFailed, match="ZIP64"):
        zipguard.open_checked(forged, ExtractLimits())
    # Same answer as any other corrupt zip under an Office name: not an Office file.
    assert file_class(forged, ExtractLimits()) is FileClass.UNSUPPORTED


def test_memory_error_in_the_zip_parser_is_not_an_invalid_zip(
    docs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import types

    from osa.extract import zipguard

    def out_of_memory(*args: object, **kwargs: object) -> None:
        raise MemoryError

    fake = types.SimpleNamespace(**{**vars(zipfile), "ZipFile": out_of_memory})
    monkeypatch.setattr(zipguard, "zipfile", fake)

    with pytest.raises(MemoryError):
        zipguard.open_checked(docs["contrato.docx"], ExtractLimits())


def test_memory_error_on_the_ooxml_path_reaches_the_worker_answer(
    tmp_path: Path, docs: dict[str, Path]
) -> None:
    script = (
        "import types, zipfile\n"
        "from osa.extract import worker, zipguard\n"
        "def out_of_memory(*args, **kwargs):\n"
        "    raise MemoryError\n"
        "zipguard.zipfile = types.SimpleNamespace(**{**vars(zipfile), 'ZipFile': out_of_memory})\n"
        "raise SystemExit(worker.main())\n"
    )

    with pytest.raises(ExtractionFailed, match="memória insuficiente"):
        run_worker(docs["contrato.docx"], fast_limits(), argv=[PYTHON, "-I", "-c", script])
