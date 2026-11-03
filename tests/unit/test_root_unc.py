"""O caminho de rede (UNC) de uma raiz: forma canônica e recusas (TASK-110)."""

from __future__ import annotations

import pytest

from osa.admin.services import MAX_UNC, root_unc


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        (r"\\srv\acervo", r"\\srv\acervo"),
        (r"\\srv\acervo\Documentos\\", r"\\srv\acervo\Documentos"),
        ("//srv/acervo/docs/", r"\\srv\acervo\docs"),
        (r"  \\srv\Acervo da Empresa  ", r"\\srv\Acervo da Empresa"),
        (r"\\srv\Conteúdo", r"\\srv\Conteúdo"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_unc_is_stored_in_canonical_form(raw: str | None, canonical: str | None) -> None:
    assert root_unc(raw) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        r"\\srv",
        r"\\srv\\",
        r"srv\acervo",
        "/Volumes/acervo",
        r"\\srv\..",
        r"\\srv\.\x",
        r"\\srv\a:b",
        r"\\srv\a|b",
        "\\\\srv\\a\x00b",
        "\\\\srv\\a\nb",
        "\\\\srv\\" + "a" * MAX_UNC,
    ],
)
def test_malformed_unc_is_refused_with_the_text_the_manual_quotes(raw: str) -> None:
    with pytest.raises(ValueError, match="^caminho de rede inválido$"):
        root_unc(raw)
