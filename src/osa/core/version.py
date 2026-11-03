"""The version a running installation reports (``/status``, every log line; TASK-112).

The package version plus, when the code runs from a git checkout (the Mac mini installs one in
``/opt/osa``), the short commit, read from ``.git`` without running git.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from osa import __version__

_SHORT = 7


def _commit(start: Path) -> str | None:
    for folder in (start, *start.parents):
        git = folder / ".git"
        if not git.is_dir():
            continue
        try:
            head = (git / "HEAD").read_text(encoding="utf-8").strip()
            if not head.startswith("ref: "):
                return head[:_SHORT] or None
            ref = head[5:]
            loose = git / ref
            if loose.is_file():
                return loose.read_text(encoding="utf-8").strip()[:_SHORT] or None
            packed = git / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    sha, _, name = line.partition(" ")
                    if name == ref:
                        return sha[:_SHORT]
        except OSError:
            return None
        return None
    return None


@lru_cache(maxsize=1)
def full_version() -> str:
    """``0.1.0`` or ``0.1.0+abc1234`` (the installed commit)."""
    commit = _commit(Path(__file__).resolve().parent)
    return f"{__version__}+{commit}" if commit else __version__
