"""Masking of URL userinfo passwords in free text (shared by settings and logging).

Fail-closed rule: for each ``scheme://`` the authority runs up to the first ``/``,
``?``, ``#`` or whitespace. If it contains ``@``, everything before the LAST ``@`` is
userinfo territory; when a ``:`` appears there, the text from that first ``:`` up to
the last ``@`` becomes ``:[REDACTED]``. No structure guessing (commas, multiple hosts,
``@`` inside the password) can leak a password this way.

Trade-off: in a multi-host DSN (``scheme://a:p1@h1,b:p2@h2/db``) the intermediate
hosts and users are masked together with the passwords (``scheme://a:[REDACTED]@h2/db``).

Limitation: a password containing an unencoded ``/``, ``?``, ``#`` or whitespace ends
the authority before the ``@`` and is not recognised (valid URLs percent-encode them).
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_URL_AUTHORITY = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<authority>[^/?#\s]*)")


def _mask_authority(match: re.Match[str]) -> str:
    authority = match["authority"]
    userinfo, at, host = authority.rpartition("@")
    if not at:
        return match[0]
    user, colon, _secret = userinfo.partition(":")
    if not colon:
        return match[0]
    return f"{match['scheme']}{user}:{REDACTED}@{host}"


def scrub_url_passwords(text: str) -> str:
    """Mask the userinfo password of every URL authority in ``text`` (fail-closed)."""
    return _URL_AUTHORITY.sub(_mask_authority, text)
