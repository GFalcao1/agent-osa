"""Prova native do filesystem seguro contra um share SMB de teste (TASK-104).

Roda só com ``OSA_TEST_SHARE_ROOT`` num mount ``smbfs`` (ver ``conftest.py``); sem isso, todo
teste é pulado com o motivo e a suite fica PENDENTE. Só arquivos fictícios; nenhum teste apaga.

Os testes de medição (limite de nome, limite de caminho, inode) registram o que o share fez no
resumo final, para o "Registro de execução" e o ADR-0011. Os que comparam com as constantes do
produto falham se o share medir diferente.
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
from mounts import parse_mount

from osa.domain.errors import UnsafePath
from osa.fs import archive as archive_module
from osa.fs import names
from osa.fs.archive import archive, undo
from osa.fs.errors import CrossDevice, CrossRoot, NameTooLong, NoReplaceUnavailable
from osa.fs.names import MAX_NAME_UNITS, QUARANTINE_DIR
from osa.fs.noreplace import Primitive, proven_primitive, rename_noreplace, volume_capabilities
from osa.fs.quarantine import ensure_quarantine, new_quarantine_file
from osa.fs.roots import open_beneath, open_root, resolve_within
from osa.fs.template import DepartmentFolder, ensure_owner_tree

pytestmark = pytest.mark.native

Note = Callable[[str], None]
PAYLOAD = bytes(range(256)) * 4096  # 1 MiB fictício e determinístico


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def put(path: Path, content: bytes = b"conteudo ficticio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def tree(work: Path) -> Path:
    """``raiz/Alfa/Fiscal`` e a quarentena, dentro do share."""
    (work / "Alfa" / "Fiscal").mkdir(parents=True)
    (work / QUARANTINE_DIR).mkdir()
    return work


@pytest.fixture
def exclusive_rename(work: Path) -> None:
    """Pula os testes de efeito quando o share não comprova rename exclusivo.

    ``test_capability_e_backend_de_rename_exclusivo`` é quem falha nesse caso.
    """
    fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY)
    try:
        proven_primitive(fd)
    except NoReplaceUnavailable as error:
        pytest.skip(f"share sem rename exclusivo comprovado: {error}")
    finally:
        os.close(fd)


# --- capability e backend --------------------------------------------------------------------


def test_capability_e_backend_de_rename_exclusivo(work: Path, note: Note) -> None:
    fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY)
    try:
        caps = volume_capabilities(fd)
        note(f"capabilities do volume: {caps}")
        try:
            primitive = proven_primitive(fd)
        except NoReplaceUnavailable as error:
            note(f"backend NÃO comprovado: {error}")
            pytest.fail(
                "o share não comprova rename exclusivo: o produto recusa arquivar nele; a task "
                f"não fecha sem decisão humana (servidor, versão ou montagem diferentes): {error}"
            )
    finally:
        os.close(fd)
    note(f"backend comprovado: {primitive.value}")
    assert primitive in set(Primitive)


@pytest.mark.usefixtures("exclusive_rename")
def test_colisao_no_nivel_da_primitiva_preserva_bytes(work: Path) -> None:
    """Prova que a capability não mente: o servidor recusa mesmo, sem substituir."""
    put(work / "a", b"A")
    put(work / "b", b"B")
    fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY)
    try:
        outcome = rename_noreplace(fd, "a", fd, "b")
    finally:
        os.close(fd)
    assert outcome.renamed is False
    assert (work / "b").read_bytes() == b"B"
    assert (work / "a").read_bytes() == b"A"


# --- arquivar --------------------------------------------------------------------------------


@pytest.mark.usefixtures("exclusive_rename")
def test_rename_atomico_preserva_conteudo(tree: Path, note: Note) -> None:
    put(tree / QUARANTINE_DIR / "u1", PAYLOAD)
    result = archive(tree, f"{QUARANTINE_DIR}/u1", "Alfa/Fiscal", "nota.pdf", effects_enabled=True)
    note(f"primitiva usada: {result.primitive}")
    destination = tree / "Alfa" / "Fiscal" / "nota.pdf"
    assert result.path == destination
    assert sha(destination.read_bytes()) == sha(PAYLOAD)
    assert destination.stat().st_size == len(PAYLOAD) == result.size
    assert not (tree / QUARANTINE_DIR / "u1").exists()
    assert sorted(p.name for p in (tree / QUARANTINE_DIR).iterdir()) == []
    assert sorted(p.name for p in (tree / "Alfa" / "Fiscal").iterdir()) == ["nota.pdf"]


@pytest.mark.usefixtures("exclusive_rename")
def test_colisao_gera_sufixo_e_nunca_substitui(tree: Path) -> None:
    original = put(tree / "Alfa" / "Fiscal" / "nota.pdf", b"original")
    finals = []
    for index in range(3):
        put(tree / QUARANTINE_DIR / f"u{index}", f"novo{index}".encode())
        result = archive(
            tree, f"{QUARANTINE_DIR}/u{index}", "Alfa/Fiscal", "nota.pdf", effects_enabled=True
        )
        finals.append(result.final_name)
        assert result.path.read_bytes() == f"novo{index}".encode()
    assert finals == ["nota (2).pdf", "nota (3).pdf", "nota (4).pdf"]
    assert original.read_bytes() == b"original"


@pytest.mark.usefixtures("exclusive_rename")
def test_corrida_entre_processos_nao_perde_arquivo(tree: Path) -> None:
    count = 8
    for index in range(count):
        put(tree / QUARANTINE_DIR / f"u{index}", f"conteudo-{index}".encode())
    with ProcessPoolExecutor(max_workers=count) as pool:
        futures = [
            pool.submit(
                archive,
                tree,
                f"{QUARANTINE_DIR}/u{index}",
                "Alfa/Fiscal",
                "doc.pdf",
                effects_enabled=True,
            )
            for index in range(count)
        ]
        results = [future.result() for future in futures]
    assert len({r.final_name for r in results}) == count
    for index, result in enumerate(results):
        assert result.path.read_bytes() == f"conteudo-{index}".encode()
    assert sorted(p.name for p in (tree / QUARANTINE_DIR).iterdir()) == []


@pytest.mark.usefixtures("exclusive_rename")
def test_acentos_forma_unicode_e_caixa(tree: Path, note: Note) -> None:
    nfc = "ação.pdf"
    nfd = unicodedata.normalize("NFD", nfc)
    put(tree / QUARANTINE_DIR / "a", b"primeiro")
    first = archive(tree, f"{QUARANTINE_DIR}/a", "Alfa/Fiscal", nfd, effects_enabled=True)
    stored = sorted(p.name for p in (tree / "Alfa" / "Fiscal").iterdir())
    note(
        f"gravado como NFC={[n == nfc for n in stored]} NFD={[n == nfd for n in stored]}; "
        f"exists(NFC)={os.path.exists(tree / 'Alfa' / 'Fiscal' / nfc)} "
        f"exists(NFD)={os.path.exists(tree / 'Alfa' / 'Fiscal' / nfd)}"
    )
    assert first.final_name == nfc  # todo nome gravado é NFC
    put(tree / QUARANTINE_DIR / "b", b"segundo")
    cross = archive(tree, f"{QUARANTINE_DIR}/b", "Alfa/Fiscal", "AÇÃO.PDF", effects_enabled=True)
    assert cross.final_name == "AÇÃO (2).PDF"  # caixa e forma colidem pela chave
    assert first.path.read_bytes() == b"primeiro"
    assert cross.path.read_bytes() == b"segundo"
    # pasta de destino em outra caixa/forma é reaproveitada, não duplicada
    put(tree / QUARANTINE_DIR / "c", b"terceiro")
    third = archive(tree, f"{QUARANTINE_DIR}/c", "ALFA/fiscal", "x.pdf", effects_enabled=True)
    assert third.dest_rel == "Alfa/Fiscal/x.pdf"
    assert sorted(p.name for p in tree.iterdir() if p.name.lower() == "alfa") == ["Alfa"]


@pytest.mark.usefixtures("exclusive_rename")
def test_nome_de_200_caracteres_acentuados(tree: Path, note: Note) -> None:
    name = "á" * 196 + ".pdf"
    assert len(name) == 200
    put(tree / QUARANTINE_DIR / "u", b"conteudo")
    result = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", name, effects_enabled=True)
    listed = [p.name for p in (tree / "Alfa" / "Fiscal").iterdir()]
    note(f"nome de 200 caracteres: listdir devolveu {len(listed)} entrada(s)")
    assert unicodedata.normalize("NFC", listed[0]) == name
    assert result.path.read_bytes() == b"conteudo"


# --- limites medidos -------------------------------------------------------------------------

NAME_CASES = {
    "ascii-255": "a" * 255,
    "ascii-256": "a" * 256,
    "nfc-200": "á" * 200,
    "nfc-255": "á" * 255,
    "nfc-256": "á" * 256,
    "emoji-100": "\U0001f600" * 100,
    "emoji-127": "\U0001f600" * 127,
    "emoji-128": "\U0001f600" * 128,
}


def test_limite_de_nome_medido_no_share(work: Path, note: Note) -> None:
    """Cria (sem apagar) nomes nos dois lados do limite e compara com ``MAX_NAME_UNITS``.

    Só formas NFC são comparadas: o produto nunca grava NFD. Um nome NFD (que o servidor conta
    decomposto) entra apenas como informação.
    """
    nfd = unicodedata.normalize("NFD", "á" * 128)  # 128 caracteres, 256 unidades decompostas
    try:
        put(work / nfd, b"x")
        note("nome nfd-128 (256 unidades decompostas, só informativo): aceito")
    except OSError as error:
        note(f"nome nfd-128 (256 unidades decompostas, só informativo): errno={error.errno}")
    mismatches = []
    for label, name in NAME_CASES.items():
        predicted = names.name_units(name) <= MAX_NAME_UNITS
        try:
            put(work / name, b"x")
            accepted, code = True, None
        except OSError as error:
            accepted, code = False, error.errno
        note(
            f"nome {label} ({names.name_units(name)} unidades UTF-16, "
            f"{len(name.encode())} bytes): {'aceito' if accepted else 'recusado'}"
            f"{'' if accepted else f' errno={code}'}"
        )
        if accepted != predicted:
            mismatches.append(label)
    assert not mismatches, (
        f"o limite medido do share difere de MAX_NAME_UNITS={MAX_NAME_UNITS} em {mismatches}: "
        "o valor medido vira o parâmetro max_name_units da instalação antes de fechar o gate"
    )


@pytest.mark.usefixtures("exclusive_rename")
def test_nome_acima_do_limite_e_recusado_sem_efeito_no_share(tree: Path) -> None:
    put(tree / QUARANTINE_DIR / "u", b"x")
    before = sorted(os.listdir(tree / "Alfa" / "Fiscal"))
    with pytest.raises(NameTooLong):
        archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a" * 256, effects_enabled=True)
    assert sorted(os.listdir(tree / "Alfa" / "Fiscal")) == before
    assert (tree / QUARANTINE_DIR / "u").exists()


def test_limite_de_caminho_medido_no_share(work: Path, note: Note) -> None:
    """Cria pastas aninhadas de 50 caracteres até o share recusar (ou até 400 caracteres)."""
    current = work
    relative_length = 0
    depth = 0
    refused: str | None = None
    while relative_length < 400:
        candidate = current / (f"{depth:02d}-" + "p" * 47)
        try:
            candidate.mkdir()
        except OSError as error:
            refused = f"errno={error.errno}"
            break
        current = candidate
        depth += 1
        relative_length = len(current.relative_to(work).as_posix())
    note(
        f"caminho relativo à raiz de teste: {relative_length} caracteres em {depth} níveis; "
        f"{'recusou com ' + refused if refused else 'sem recusa até 400 caracteres'}"
    )
    assert depth > 0


# --- identidade da origem --------------------------------------------------------------------


@pytest.mark.usefixtures("exclusive_rename")
def test_identidade_st_ino_e_rename_com_origem_aberta(tree: Path, note: Note) -> None:
    source = put(tree / QUARANTINE_DIR / "u", b"conteudo")
    inode = source.stat().st_ino
    note(f"IDENTITY_USES_INODE={archive_module.IDENTITY_USES_INODE}")
    # archive mantém a origem aberta durante o rename e compara a identidade depois; se o
    # servidor recusar o rename com o handle aberto, ou o inode mudar, falha aqui
    result = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a.pdf", effects_enabled=True)
    after = result.path.stat().st_ino
    note(f"st_ino antes={inode} depois={after} estável={inode == after}")
    assert result.path.read_bytes() == b"conteudo"
    if inode != after:
        pytest.fail(
            "st_ino não é estável no share: IDENTITY_USES_INODE deve virar False (e o ADR-0011 "
            "registra a identidade só por tamanho e mtime)"
        )


# --- template, quarentena, desfazer ----------------------------------------------------------


@pytest.mark.usefixtures("exclusive_rename")
def test_template_quarentena_arquivamento_e_desfazer(work: Path) -> None:
    departments = (DepartmentFolder("Fiscal", usa_ano=True), DepartmentFolder("Contábil", False))
    first = ensure_owner_tree(work, "Alfa", departments, 2026, effects_enabled=True)
    assert len(first.created) == 4
    second = ensure_owner_tree(work, "Alfa", departments, 2026, effects_enabled=True)
    assert second.created == ()
    ensure_quarantine(work, effects_enabled=True)
    assert ensure_quarantine(work, effects_enabled=True).created is False
    with new_quarantine_file(work, effects_enabled=True) as upload:
        assert upload.fd is not None
        os.write(upload.fd, PAYLOAD)
    archived = archive(work, upload.rel, "Alfa/Fiscal/2026", "contrato.pdf", effects_enabled=True)
    assert sha(archived.path.read_bytes()) == sha(PAYLOAD)
    record = archived.record(root_id=1, sha256=sha(PAYLOAD))
    undone = undo(record, effects_enabled=True)
    assert undone.dest_rel == upload.rel
    assert sha((work / upload.rel).read_bytes()) == sha(PAYLOAD)
    assert not archived.path.exists()


@pytest.mark.usefixtures("exclusive_rename")
def test_efeitos_desligados_nao_mudam_o_share(tree: Path) -> None:
    put(tree / QUARANTINE_DIR / "u", b"x")

    def listing() -> list[tuple[str, int]]:
        return sorted((str(p.relative_to(tree)), p.stat().st_size) for p in tree.rglob("*"))

    before = listing()
    simulated = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a.pdf")
    assert simulated.simulated is True
    predicted = ensure_owner_tree(tree, "Beta", (DepartmentFolder("Fiscal", False),))
    assert predicted.simulated is True
    assert listing() == before


# --- mesma raiz, links -----------------------------------------------------------------------


@pytest.mark.usefixtures("exclusive_rename")
def test_mesma_raiz_exigida(tree: Path, tmp_path: Path) -> None:
    local = put(tmp_path.resolve() / "local" / "x.pdf", b"local")
    same_volume = local.stat().st_dev == tree.stat().st_dev
    with pytest.raises(CrossRoot):
        archive(tree, local, "Alfa/Fiscal", effects_enabled=True)
    put(tree / QUARANTINE_DIR / "u", b"x")
    with pytest.raises(CrossRoot):
        archive(tree, f"{QUARANTINE_DIR}/u", tmp_path.resolve() / "local", effects_enabled=True)
    assert local.read_bytes() == b"local"
    if same_volume:
        pytest.skip("disco local no mesmo volume do share (ensaio fora do SMB): sem EXDEV")
    # no nível da primitiva, rename entre o disco local e o share é recusado pelo volume
    share_fd = os.open(tree / "Alfa" / "Fiscal", os.O_RDONLY | os.O_DIRECTORY)
    local_fd = os.open(local.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(CrossDevice):
            rename_noreplace(local_fd, "x.pdf", share_fd, "x.pdf")
    finally:
        os.close(share_fd)
        os.close(local_fd)
    assert local.read_bytes() == b"local"


@pytest.mark.usefixtures("exclusive_rename")
def test_symlink_no_share_e_recusado(tree: Path, note: Note) -> None:
    target = put(tree / "Alfa" / "alvo.txt", b"alvo")
    link = tree / "Alfa" / "atalho"
    try:
        link.symlink_to(target)
    except OSError as error:
        note(f"o share não cria symlink (errno={error.errno})")
        pytest.skip(
            f"o share não cria symlink (errno={error.errno}): a recusa de symlink no SMB fica "
            "PENDENTE; o teste com symlink local abaixo é só complementar"
        )
    folder_link = tree / "Alfa" / "pasta-link"
    folder_link.symlink_to(tree / "Alfa" / "Fiscal", target_is_directory=True)
    with pytest.raises(UnsafePath):
        archive(tree, "Alfa/atalho", "Alfa/Fiscal", effects_enabled=True)
    with pytest.raises(UnsafePath):
        resolve_within(tree, "Alfa/pasta-link")
    with pytest.raises(UnsafePath):
        archive(tree, "Alfa/alvo.txt", "Alfa/pasta-link", effects_enabled=True)
    with (
        open_root(tree) as root_fd,
        pytest.raises(UnsafePath),
        open_beneath(root_fd, ("Alfa", "pasta-link")),
    ):
        pass
    assert target.read_bytes() == b"alvo"


@pytest.mark.usefixtures("exclusive_rename")
def test_symlink_local_apontando_para_o_share_e_recusado_complementar(
    tree: Path, tmp_path: Path
) -> None:
    """COMPLEMENTAR: não prova a recusa de symlink no SMB (isso é o teste acima)."""
    target = put(tree / "Alfa" / "alvo.txt", b"alvo")
    link = tmp_path.resolve() / "atalho-da-raiz"
    link.symlink_to(tree, target_is_directory=True)
    with pytest.raises(UnsafePath), open_root(link):
        pass
    file_link = tmp_path.resolve() / "atalho-do-arquivo"
    file_link.symlink_to(target)
    with pytest.raises(CrossRoot):
        archive(tree, file_link, "Alfa/Fiscal", effects_enabled=True)
    assert target.read_bytes() == b"alvo"


# --- leitura de ``mount`` (pura; roda mesmo sem share) ----------------------------------------


def test_parse_mount_identifies_smbfs_and_the_most_specific_mount_point() -> None:
    output = "\n".join(
        [
            "/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)",
            "//GUEST:@servidor-ficticio/teste on /Volumes/teste (smbfs, nodev, mounted by u)",
            "//GUEST:@servidor-ficticio/outro on /Volumes/teste/aninhado (nfs, nodev)",
            "servidor:/srv on /mnt/dados type cifs (rw,relatime)",
            "linha que não é de mount",
        ]
    )
    assert parse_mount(output, Path("/Volumes/teste/osa")) == ("smbfs", "/Volumes/teste")
    assert parse_mount(output, Path("/Volumes/teste/aninhado/x")) == (
        "nfs",
        "/Volumes/teste/aninhado",
    )
    assert parse_mount(output, Path("/mnt/dados/x")) == ("cifs", "/mnt/dados")
    assert parse_mount(output, Path("/Users/x")) == ("apfs", "/")
    assert parse_mount("", Path("/Users/x")) is None
