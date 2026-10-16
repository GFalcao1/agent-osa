"""User roles (fixed set, ADR-0003)."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    REVIEWER = "reviewer"
