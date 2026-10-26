"""Leitura da saída de ``mount`` para identificar o filesystem do share de teste."""

from __future__ import annotations

import re
from pathlib import Path

_MOUNT_LINE = re.compile(
    r"^(?P<source>.+?) on (?P<point>/.*?) "
    r"(?:\((?P<mac>[^)]*)\)|type (?P<linux>\S+) \([^)]*\))\s*$"
)


def parse_mount(output: str, path: Path) -> tuple[str, str] | None:
    """``(tipo, ponto de montagem)`` mais específico que contém ``path``.

    Nunca devolve a origem do mount, que pode trazer usuário.
    """
    best: tuple[str, str] | None = None
    for line in output.splitlines():
        match = _MOUNT_LINE.match(line)
        if not match:
            continue
        point = match["point"]
        if path != Path(point) and Path(point) not in path.parents:
            continue
        fstype = (match["linux"] or (match["mac"] or "").split(",")[0]).strip()
        if best is None or len(point) > len(best[1]):
            best = (fstype, point)
    return best
