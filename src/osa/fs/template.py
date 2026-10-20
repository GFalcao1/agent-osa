"""Aplicação do template a um dono: cria só o que falta, nunca renomeia nem toca o que existe."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from osa.domain.errors import Conflict, NotFound
from osa.fs.names import MAX_NAME_UNITS, component_key, validate_folder_component
from osa.fs.roots import open_beneath, open_root

_MIN_YEAR = 1900
_MAX_YEAR = 2100


@dataclass(frozen=True, slots=True)
class DepartmentFolder:
    """Pasta de um departamento do template (já filtrado por ativo e ordenado por ordem)."""

    pasta: str
    usa_ano: bool


@dataclass(frozen=True, slots=True)
class TreeResult:
    owner_dir: Path
    created: tuple[Path, ...]
    existing: tuple[Path, ...]
    simulated: bool


def _levels(
    owner: str,
    departments: Sequence[DepartmentFolder],
    year: int | None,
    max_units: int,
) -> list[tuple[str, ...]]:
    if year is not None and (isinstance(year, bool) or not _MIN_YEAR <= year <= _MAX_YEAR):
        raise ValueError(f"ano fora de {_MIN_YEAR}-{_MAX_YEAR}")
    owner_name = validate_folder_component(owner, max_units=max_units)
    levels: list[tuple[str, ...]] = [(owner_name,)]
    seen: set[str] = set()
    for department in departments:
        folder = validate_folder_component(department.pasta, max_units=max_units)
        key = component_key(folder)
        if key in seen:
            raise Conflict("dois departamentos com a mesma pasta (caixa ou forma Unicode)")
        seen.add(key)
        levels.append((owner_name, folder))
        if department.usa_ano and year is not None:
            levels.append((owner_name, folder, validate_folder_component(str(year))))
    return levels


def ensure_owner_tree(
    root: Path,
    owner_folder: str,
    departments: Sequence[DepartmentFolder],
    year: int | None = None,
    *,
    effects_enabled: bool = False,
    max_name_units: int = MAX_NAME_UNITS,
) -> TreeResult:
    """Garante ``<raiz>/<dono>/<departamento>/[<ano>/]`` criando só o que falta.

    Com efeitos desligados (padrão) devolve o previsto com o estado observado, sem criar nada
    (``simulated=True``). Pasta existente em outra caixa ou forma Unicode é reaproveitada, na
    grafia gravada. Existente que é arquivo ou link é ``UnsafePath``. Departamento com
    ``usa_ano`` e ``year=None`` cria só a pasta do departamento. A raiz é aberta uma vez e todos
    os níveis nascem desse descritor. Nunca cria nem aceita ``.osa-quarentena``.
    """
    levels = _levels(owner_folder, departments, year, max_name_units)
    created: list[Path] = []
    existing: list[Path] = []
    base = Path(root)
    # grafia de cada nível (gravada se existe, pedida se ausente) e níveis previstos ausentes;
    # os níveis vêm do pai para o filho, então o pai de cada nível já foi visto
    spelled: dict[tuple[str, ...], tuple[str, ...]] = {}
    absent: set[tuple[str, ...]] = set()
    with open_root(base) as root_fd:
        for level in levels:
            parent = level[:-1]
            if parent not in absent:
                try:
                    with open_beneath(
                        root_fd, level, create=effects_enabled, max_name_units=max_name_units
                    ) as opened:
                        spelled[level] = opened.parts
                        path = base.joinpath(*opened.parts)
                        (created if opened.created[-1] else existing).append(path)
                        continue
                except NotFound:
                    if effects_enabled:
                        raise
            # simulado: o pai está previsto ausente, ou este nível não existe
            absent.add(level)
            spelled[level] = (*spelled.get(parent, parent), level[-1])
            created.append(base.joinpath(*spelled[level]))
    owner_dir = base.joinpath(*spelled[levels[0]])
    return TreeResult(
        owner_dir=owner_dir,
        created=tuple(created),
        existing=tuple(existing),
        simulated=not effects_enabled,
    )
