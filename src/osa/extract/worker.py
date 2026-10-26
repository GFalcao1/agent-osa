"""Extraction worker: runs in a child process (``python -I -m osa.extract.worker``).

Reads ``{"path": ..., "limits": {...}}`` as JSON on stdin and writes one JSON
document on the original stdout: either ``{"ok": true, "content": {...}}`` or
``{"ok": false, "kind": "unsupported" | "failed", "message": ...}``. Library noise
is redirected to stderr. The worker has no network: sockets are disabled before
any document is parsed. Messages never carry document content.
"""

from __future__ import annotations

import base64
import json
import math
import os
import socket
import stat
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from osa.domain.document import FileClass
from osa.domain.errors import DomainError, ExtractionFailed, SensitiveFile, Unsupported
from osa.extract.classify import class_by_name, file_class, normalized_extension
from osa.extract.model import ExtractedContent, ExtractLimits


def extract_in_process(path: Path, limits: ExtractLimits) -> ExtractedContent:
    """Check the name, then basic file properties, then sniff and dispatch to the backend.

    The size is checked before the content sniff, so an oversized file is never parsed.
    """
    by_name = class_by_name(path.name, limits.sensitive_extensions)
    if by_name is FileClass.SENSITIVE:
        raise SensitiveFile("arquivo de classe sensível")
    if by_name is FileClass.UNSUPPORTED:
        raise Unsupported("formato não suportado")
    try:
        info = os.stat(path)
    except OSError as exc:
        raise ExtractionFailed("arquivo ilegível") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ExtractionFailed("não é um arquivo comum")
    if info.st_size > limits.max_file_bytes:
        raise ExtractionFailed("arquivo grande demais")
    cls = file_class(path, limits)
    if cls is FileClass.UNSUPPORTED:
        raise Unsupported("formato não suportado ou conteúdo diferente da extensão")
    if cls is FileClass.PDF:
        from osa.extract.pdf import extract_pdf

        return extract_pdf(path, limits)
    if cls is FileClass.IMAGE:
        from osa.extract.image import extract_image

        return extract_image(path, limits)
    from osa.extract import office

    if normalized_extension(path.name) == ".docx":
        return office.extract_docx(path, limits)
    return office.extract_xlsx(path, limits)


def _encode(content: ExtractedContent) -> dict[str, Any]:
    return {
        "text": content.text,
        "images": [
            {
                "data": base64.b64encode(image.data).decode("ascii"),
                "page": image.page,
                "width": image.width,
                "height": image.height,
            }
            for image in content.images
        ],
        "pages_total": content.pages_total,
        "truncated": content.truncated,
        "method": content.method,
    }


def _disable_network() -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("network access is disabled in the extraction worker")

    for name in ("create_connection", "getaddrinfo", "gethostbyname"):
        setattr(socket, name, refuse)
    for name in ("connect", "connect_ex", "sendto"):
        setattr(socket.socket, name, refuse)


def cpu_seconds(limits: ExtractLimits) -> int:
    """CPU-time cap: RLIMIT_CPU counts every thread, and OCR runs ``OCR_THREADS`` of them.

    The wall-clock timeout of the parent stays the effective limit; this is the backstop.
    """
    from osa.extract.ocr import OCR_THREADS

    return math.ceil(limits.timeout_s * OCR_THREADS) + 5


def _apply_rlimits(limits: ExtractLimits) -> None:
    """Best effort: no core dumps and a CPU-time cap (memory is capped by the parent)."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        _soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
        seconds = cpu_seconds(limits)
        if hard != resource.RLIM_INFINITY:
            seconds = min(seconds, hard)
        resource.setrlimit(resource.RLIMIT_CPU, (seconds, hard))
    except (ImportError, ValueError, OSError):
        pass


def main() -> int:
    out_fd = os.dup(1)
    os.dup2(2, 1)  # anything a library prints goes to stderr
    try:
        request = json.loads(sys.stdin.read())
        limits = limits_from_json(request["limits"])
        path = Path(request["path"])
        _disable_network()
        _apply_rlimits(limits)
        content = extract_in_process(path, limits)
        payload: dict[str, Any] = {"ok": True, "content": _encode(content)}
    except Unsupported as exc:
        payload = {"ok": False, "kind": "unsupported", "message": str(exc)}
    except SensitiveFile as exc:
        payload = {"ok": False, "kind": "sensitive", "message": str(exc)}
    except DomainError as exc:
        payload = {"ok": False, "kind": "failed", "message": str(exc)}
    except MemoryError:
        payload = {"ok": False, "kind": "failed", "message": "memória insuficiente"}
    except Exception as exc:
        payload = {
            "ok": False,
            "kind": "failed",
            "message": f"falha inesperada na extração ({type(exc).__name__})",
        }
    with os.fdopen(out_fd, "w", encoding="utf-8") as out:
        json.dump(payload, out)
    return 0


def limits_from_json(data: dict[str, Any]) -> ExtractLimits:
    return ExtractLimits(**{**data, "sensitive_extensions": tuple(data["sensitive_extensions"])})


def limits_to_json(limits: ExtractLimits) -> dict[str, Any]:
    data = asdict(limits)
    data["sensitive_extensions"] = list(limits.sensitive_extensions)
    return data


if __name__ == "__main__":
    raise SystemExit(main())
