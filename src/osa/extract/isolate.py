"""Parent side of the extraction worker: spawn, time limit, memory limit, validation.

A hostile or pathological file can hang or exhaust the memory of a parser, and a
``try/except`` cannot stop that. Parsing therefore runs in a child process in its
own session, with a minimal environment, a private temporary directory, a
wall-clock timeout, a capped output and a resident memory watchdog that kills
the whole process group. Whatever the child does, the caller sees
``ExtractionFailed``. Only the standard library is used.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import IO, Any

from osa.domain.errors import ExtractionFailed, SensitiveFile, Unsupported
from osa.extract.model import METHODS, ExtractedContent, ExtractLimits, Method, PageImage
from osa.extract.worker import limits_to_json

_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "LC_CTYPE")
_MAX_OUTPUT = 128 * 1024 * 1024
_CHUNK = 1024 * 1024
_POLL_SECONDS = 0.25
_MAX_SAMPLE_FAILURES = 3  # consecutive ps failures tolerated after a first good sample
_PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")


def worker_argv() -> list[str]:
    """Command that starts the worker; ``-I`` ignores PYTHON* variables, ``-B`` writes no .pyc."""
    return [sys.executable, "-I", "-B", "-m", "osa.extract.worker"]


def _child_env(scratch: str) -> dict[str, str]:
    """Locale and PATH only, plus a private TMPDIR (onnxruntime writes ``$TMPDIR/.ses``)."""
    env = {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}
    env["TMPDIR"] = scratch
    return env


def group_rss_mb(pgid: int) -> float | None:
    """Resident memory, in MiB, of every process in the group; None when ``ps`` fails.

    ``ps -A -o pgid=,rss=`` is POSIX (macOS and Linux). A group with no process left is 0.0.
    """
    try:
        done = subprocess.run(
            ["ps", "-A", "-o", "pgid=,rss="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    total_kib = 0
    for line in done.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == str(pgid) and fields[1].isdigit():
            total_kib += int(fields[1])
    return total_kib / 1024


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill the worker's group, only while the worker is not reaped (its pid is not reusable)."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _watch_memory(
    proc: subprocess.Popen[bytes],
    limit_mb: int,
    stop: threading.Event,
    hit: threading.Event,
    blind: threading.Event,
) -> None:
    """Sample the group's memory; a watchdog that cannot see also stops the worker."""
    failures = 0
    sampled = False
    while proc.poll() is None:
        rss = group_rss_mb(proc.pid)
        if rss is None:
            failures += 1
            if not sampled or failures >= _MAX_SAMPLE_FAILURES:
                blind.set()
                _kill_group(proc)
                return
        else:
            sampled, failures = True, 0
            if rss > limit_mb:
                hit.set()
                _kill_group(proc)
                return
        if stop.wait(_POLL_SECONDS):
            return


def _read_capped(
    stream: IO[bytes], proc: subprocess.Popen[bytes], sink: bytearray, over: threading.Event
) -> None:
    """Read the worker's answer; past ``_MAX_OUTPUT`` bytes the worker is killed."""
    while True:
        chunk = stream.read(_CHUNK)
        if not chunk:
            return
        if len(sink) + len(chunk) > _MAX_OUTPUT:
            over.set()
            _kill_group(proc)
            return
        sink += chunk


def _decode(payload: Any, limits: ExtractLimits) -> ExtractedContent:
    """Strictly validate the worker output; it is data, not trusted code or pickle."""
    try:
        if not isinstance(payload, dict):
            raise TypeError("payload")
        if payload.get("ok") is not True:
            kind = payload.get("kind")
            message = str(payload.get("message", ""))[:200]
            if kind == "unsupported":
                raise Unsupported(message)
            if kind == "sensitive":
                raise SensitiveFile(message)
            raise ExtractionFailed(message or "falha na extração")
        data = payload["content"]
        method = data["method"]
        text = data["text"]
        raw_images = data["images"]
        pages_total = data["pages_total"]
        truncated = data["truncated"]
        if (
            method not in METHODS
            or not isinstance(text, str)
            or len(text) > limits.max_text_chars
            or not isinstance(raw_images, list)
            or len(raw_images) > limits.max_image_pages
            or isinstance(pages_total, bool)
            or not isinstance(pages_total, int)
            or pages_total < 1
            or not isinstance(truncated, bool)
        ):
            raise ValueError("shape")
        images: list[PageImage] = []
        for item in raw_images:
            blob = base64.b64decode(item["data"], validate=True)
            page, width, height = item["page"], item["width"], item["height"]
            if (
                not blob.startswith(_PNG_MAGIC)
                or not all(
                    isinstance(n, int) and not isinstance(n, bool) for n in (page, width, height)
                )
                or page < 1
                or width < 1
                or height < 1
            ):
                raise ValueError("image")
            images.append(PageImage(data=blob, page=page, width=width, height=height))
        method_value: Method = method
        return ExtractedContent(
            text=text,
            images=tuple(images),
            pages_total=pages_total,
            truncated=truncated,
            method=method_value,
        )
    except (Unsupported, SensitiveFile, ExtractionFailed):
        raise
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise ExtractionFailed("resposta inválida do processo de extração") from exc


def run_worker(
    path: Path, limits: ExtractLimits, argv: list[str] | None = None
) -> ExtractedContent:
    """Run the worker on ``path`` and return its validated result."""
    request = json.dumps({"path": str(path), "limits": limits_to_json(limits)}).encode()
    scratch = tempfile.mkdtemp(prefix="osa-extract-")
    try:
        return _run(request, limits, argv or worker_argv(), scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)  # our own directory, made above


def _run(request: bytes, limits: ExtractLimits, argv: list[str], scratch: str) -> ExtractedContent:
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # library noise is not kept in the parent's memory
            env=_child_env(scratch),
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise ExtractionFailed("não foi possível iniciar o processo de extração") from exc
    assert proc.stdin is not None and proc.stdout is not None
    stop = threading.Event()
    over_memory = threading.Event()
    blind = threading.Event()
    over_output = threading.Event()
    output = bytearray()
    watchdog = threading.Thread(
        target=_watch_memory,
        args=(proc, limits.max_memory_mb, stop, over_memory, blind),
        daemon=True,
    )
    reader = threading.Thread(
        target=_read_capped, args=(proc.stdout, proc, output, over_output), daemon=True
    )
    watchdog.start()
    reader.start()
    try:
        try:
            proc.stdin.write(request)
            proc.stdin.close()
        except OSError:
            pass  # the worker is already gone; its exit status tells why
        try:
            proc.wait(timeout=limits.timeout_s)
        except subprocess.TimeoutExpired as exc:
            _kill_group(proc)
            proc.wait()
            raise ExtractionFailed("tempo limite de extração excedido") from exc
    finally:
        stop.set()
        watchdog.join(timeout=5)
        reader.join(timeout=5)
        if reader.is_alive():
            # A process left in the group still holds the pipe after the worker exited;
            # while it lives the group id cannot be reused, so signalling it is safe.
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
            reader.join(timeout=5)
        if not reader.is_alive():
            proc.stdout.close()
    if over_memory.is_set():
        raise ExtractionFailed("limite de memória de extração excedido")
    if blind.is_set():
        raise ExtractionFailed("vigia de memória indisponível")
    if over_output.is_set():
        raise ExtractionFailed("resposta grande demais do processo de extração")
    if proc.returncode == -signal.SIGXCPU:
        raise ExtractionFailed("tempo de CPU excedido no processo de extração")
    try:
        payload = json.loads(bytes(output))
    except ValueError as exc:
        raise ExtractionFailed(
            f"processo de extração terminou de forma anormal (código {proc.returncode})"
        ) from exc
    return _decode(payload, limits)
