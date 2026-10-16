"""YAML export and import rules that need no database."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from osa.admin import export

SENTINEL = "fictional-key-not-real"


def make_cnpj(base12: str) -> str:
    def digit(digits: str, weights: list[int]) -> str:
        rest = sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
        return "0" if rest < 2 else str(11 - rest)

    first = digit(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = digit(base12 + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + first + second


VALID = """\
version: 1
raizes:
  - caminho: /Volumes/ficticio/raiz
    descricao: Raiz fictícia
    ativo: true
    pastas_monitoradas:
      - caminho: entrada
        ativo: true
departamentos:
  - nome: Fiscal
    pasta: Fiscal
    usa_ano: true
    ativo: true
  - nome: Pessoal
    pasta: Pessoal
    usa_ano: false
    ativo: true
donos:
  - nome_fantasia: Alfa
    razao_social: Alfa Ltda
    cnpj: "CNPJ_PLACEHOLDER"
    pasta: Alfa
    ativo: true
    aliases: [alfa, alfa ltda]
usuarios:
  - telegram_id: 1001
    nome: Admin Ficticio
    papel: admin
    departamento: null
    ativo: true
  - telegram_id: 2002
    nome: Maria Ficticia
    papel: operator
    departamento: Fiscal
    ativo: true
modelo:
  nome: modelo-ficticio:1b
  digest: sha256:abc
  provedor_externo_ativo: false
""".replace("CNPJ_PLACEHOLDER", make_cnpj("112223330001"))


def _registry() -> export.Registry:
    return export.parse_registry_yaml(VALID)


def test_parse_valid_document() -> None:
    registry = _registry()

    assert registry.version == 1
    assert [d.nome for d in registry.departamentos] == ["Fiscal", "Pessoal"]
    assert registry.raizes[0].pastas_monitoradas[0].caminho == "entrada"
    assert registry.donos[0].aliases == ["alfa", "alfa ltda"]
    assert registry.usuarios[1].departamento == "Fiscal"
    assert registry.modelo is not None and registry.modelo.provedor_externo_ativo is False


def test_empty_sections_are_optional() -> None:
    registry = export.parse_registry_yaml("version: 1\n")

    assert registry.raizes == [] and registry.usuarios == [] and registry.modelo is None


def test_dump_then_parse_round_trips_and_keeps_order_and_accents() -> None:
    registry = _registry()

    text = export.dump_registry_yaml(registry)

    assert export.parse_registry_yaml(text) == registry
    assert text.startswith("version: 1\n")
    assert text.index("raizes:") < text.index("departamentos:") < text.index("donos:")
    assert "Raiz fictícia" in text  # allow_unicode: no \u escapes
    assert "!!python" not in text


def test_dump_goes_through_safe_dump_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    real_safe_dump = yaml.safe_dump

    def spy(data: object, *args: Any, **kwargs: Any) -> Any:
        calls.append(data)
        return real_safe_dump(data, *args, **kwargs)

    def unsafe(*args: object, **kwargs: object) -> None:
        raise AssertionError("yaml.dump can emit python tags: only safe_dump is allowed")

    monkeypatch.setattr(yaml, "safe_dump", spy)
    monkeypatch.setattr(yaml, "dump", unsafe)

    text = export.dump_registry_yaml(_registry())

    assert len(calls) == 1 and "papel: admin" in text


@pytest.mark.parametrize(
    "document",
    [
        "version: 1\nraizes: &a []\n",
        "version: 1\nraizes: &a []\ndepartamentos: *a\n",
        "version: &v 1\n",
        "&a [*a]\n",
        "version: 1\nmodelo: &m {nome: x}\nusuarios: []\ndonos: *m\n",
    ],
)
def test_anchors_and_aliases_are_rejected(document: str) -> None:
    with pytest.raises(ValueError, match="âncoras"):
        export.parse_registry_yaml(document)


def test_recursive_alias_cannot_hang_or_pass() -> None:
    with pytest.raises(ValueError, match="âncoras"):
        export.parse_registry_yaml("a: &x\n  b: *x\n")


def test_duplicate_keys_and_merge_keys_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicada"):
        export.parse_registry_yaml("version: 1\nversion: 1\n")
    with pytest.raises(ValueError, match="mesclagem"):
        export.parse_registry_yaml("version: 1\n<<: {raizes: []}\n")


@pytest.mark.parametrize(
    "key",
    ["token", "telegram_bot_token", "password", "passwd", "secret", "api_key", "api-key",
     "apikey", "external_llm_api_key", "Authorization", "SECRET_THING"],
)  # fmt: skip
def test_sensitive_keys_are_rejected_at_any_depth_without_echoing_values(key: str) -> None:
    for document in (
        f"version: 1\n{key}: {SENTINEL}\n",
        f"version: 1\nmodelo:\n  nome: x\n  {key}: {SENTINEL}\n",
        f"version: 1\ndonos:\n  - nome_fantasia: A\n    pasta: A\n    {key}: {SENTINEL}\n",
        "version: 1\nusuarios:\n  - {telegram_id: 1, nome: A, papel: admin, "
        f"{key}: {SENTINEL}}}\n",
    ):
        with pytest.raises(ValueError, match="chave sensível") as excinfo:
            export.parse_registry_yaml(document)
        assert SENTINEL not in str(excinfo.value)


def test_sensitive_key_inside_a_list_of_lists_is_found() -> None:
    with pytest.raises(ValueError, match="chave sensível"):
        export.parse_registry_yaml(f"version: 1\nx:\n  - - [{{api_key: {SENTINEL}}}]\n")


SECRET_AS_KEY = "sk-fictional-0123456789abcdef"  # a credential pasted where a key goes
SENSITIVE_SECRET_AS_KEY = "fictional_api_key_0123456789abcdef"  # also matches SENSITIVE_KEY


def _documents_with_key(key: str) -> list[str]:
    return [
        f"version: 1\n{key}: x\n",
        f"version: 1\nmodelo:\n  nome: m\n  {key}: x\n",
        "version: 1\nraizes:\n  - caminho: /r\n    pastas_monitoradas:\n"
        f"      - {{caminho: a, {key}: x}}\n",
        f"version: 1\ndonos:\n  - nome_fantasia: A\n    pasta: A\n    {key}: x\n",
        f"version: 1\nx:\n  - - {{{key}: 1}}\n",
        f"version: 1\nmodelo:\n  {key}:\n    nome: m\n",
    ]


@pytest.mark.parametrize("key", [SECRET_AS_KEY, SENSITIVE_SECRET_AS_KEY])
def test_errors_never_echo_an_unknown_or_sensitive_key(key: str) -> None:
    for document in _documents_with_key(key):
        with pytest.raises(ValueError) as excinfo:
            export.parse_registry_yaml(document)
        message = str(excinfo.value)
        assert key not in message, document
        assert "fictional" not in message and "0123456789" not in message


def test_error_paths_keep_known_field_names_and_positions() -> None:
    with pytest.raises(ValueError) as excinfo:
        export.parse_registry_yaml(
            f"version: 1\ndonos:\n  - nome_fantasia: A\n    pasta: A\n    {SECRET_AS_KEY}: x\n"
        )

    assert "donos.0" in str(excinfo.value)


def test_unknown_keys_and_wrong_types_are_rejected_without_echoing_input() -> None:
    with pytest.raises(ValueError) as excinfo:
        export.parse_registry_yaml(f"version: 1\nextra: {SENTINEL}\n")
    assert SENTINEL not in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        export.parse_registry_yaml(
            f"version: 1\nusuarios:\n  - telegram_id: {SENTINEL}\n    nome: A\n    papel: admin\n"
        )
    assert SENTINEL not in str(excinfo.value)
    assert "telegram_id" in str(excinfo.value)


@pytest.mark.parametrize(
    "document",
    [
        "",
        "- a\n- b\n",
        "just text\n",
        "version: 2\n",
        "version: '1'\n",
        "version: 1\nusuarios:\n  - {telegram_id: 0, nome: A, papel: admin}\n",
        "version: 1\nusuarios:\n  - {telegram_id: 5, nome: A, papel: root}\n",
        "version: 1\nusuarios:\n  - {telegram_id: '5', nome: A, papel: admin}\n",
        "version: 1\nusuarios:\n  - {telegram_id: 5, nome: A, papel: operator}\n",
        "version: 1\nusuarios:\n  - {telegram_id: 5, nome: A, papel: admin}\n"
        "  - {telegram_id: 5, nome: B, papel: admin}\n",
        "version: 1\nusuarios:\n  - {telegram_id: 5, nome: A, papel: operator, departamento: Z}\n",
        "version: 1\ndepartamentos:\n  - {nome: A, pasta: A}\n  - {nome: a, pasta: B}\n",
        "version: 1\nraizes:\n  - {caminho: /a}\n  - {caminho: /A}\n",
        "version: 1\nmodelo: {nome: x, provedor_externo_ativo: 'yes'}\n",
        "version: 1\nraizes: [unclosed\n",
        "version: !!python/object/apply:os.system ['true']\n",
        "version: 1\nwhen: 2026-10-16\n",
    ],
)
def test_malformed_documents_are_rejected(document: str) -> None:
    with pytest.raises(ValueError):
        export.parse_registry_yaml(document)


def test_yaml_syntax_error_does_not_echo_the_offending_line() -> None:
    with pytest.raises(ValueError) as excinfo:
        export.parse_registry_yaml(f"version: 1\nraizes: [{SENTINEL}\n")

    assert SENTINEL not in str(excinfo.value)
    assert "linha" in str(excinfo.value)


def test_oversized_documents_are_rejected_before_parsing() -> None:
    with pytest.raises(ValueError, match="grande demais"):
        export.parse_registry_yaml("version: 1\n# " + "x" * export.MAX_BYTES + "\n")


def test_deeply_nested_documents_are_rejected_cleanly() -> None:
    document = "version: 1\nx: " + "[" * 5000 + "]" * 5000 + "\n"

    with pytest.raises(ValueError):
        export.parse_registry_yaml(document)


def test_export_whitelist_has_no_secret_looking_field() -> None:
    names: set[str] = set()

    def walk(model: type[Any]) -> None:
        for name, field in model.model_fields.items():
            names.add(name)
            for arg in (field.annotation, *getattr(field.annotation, "__args__", ())):
                inner = getattr(arg, "__args__", ())
                for candidate in (arg, *inner):
                    if hasattr(candidate, "model_fields"):
                        walk(candidate)

    walk(export.Registry)

    assert not any(export.SENSITIVE_KEY.search(name) for name in names), names


def test_write_export_file_creates_private_file_and_refuses_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "cadastro.yaml"

    export.write_export_file(target, "version: 1\n")

    assert target.read_text() == "version: 1\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        export.write_export_file(target, "outro\n")
    assert target.read_text() == "version: 1\n"


def test_write_export_file_never_follows_a_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "vitima.txt"
    victim.write_text("intacto")
    link = tmp_path / "link.yaml"
    link.symlink_to(victim)

    with pytest.raises(OSError):
        export.write_export_file(link, "version: 1\n")

    assert victim.read_text() == "intacto"


def test_read_registry_file_reads_regular_files_only(tmp_path: Path) -> None:
    good = tmp_path / "ok.yaml"
    good.write_text(VALID)
    link = tmp_path / "link.yaml"
    link.symlink_to(good)
    big = tmp_path / "big.yaml"
    big.write_bytes(b"#" * (export.MAX_BYTES + 1))
    binary = tmp_path / "bin.yaml"
    binary.write_bytes(b"\xff\xfe\x00")

    assert export.read_registry_file(good) == VALID
    for bad, message in ((link, "link"), (big, "grande"), (binary, "UTF-8"), (tmp_path, "arquivo")):
        with pytest.raises(ValueError, match=message):
            export.read_registry_file(bad)
    with pytest.raises(ValueError, match="não encontrado"):
        export.read_registry_file(tmp_path / "nada.yaml")


def test_export_file_creation_uses_exclusive_no_follow_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []
    real_open = os.open

    def spy(path: str | os.PathLike[str], flags: int, mode: int = 0o777, **kw: Any) -> int:
        seen.append(flags)
        return real_open(path, flags, mode, **kw)

    monkeypatch.setattr(os, "open", spy)

    export.write_export_file(tmp_path / "x.yaml", "version: 1\n")

    assert seen[0] & os.O_EXCL and seen[0] & os.O_CREAT and seen[0] & os.O_NOFOLLOW
