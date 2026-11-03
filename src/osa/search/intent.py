"""What an operator's message turns into, decided by code (TASK-110, SPEC §5-B).

The model only fills the intent envelope; this module maps its ``acao`` to one of five
plans, chooses how a found document is delivered (the file through Telegram, its path on the
share, or a refusal) and builds the network path a user can open. Nothing here reads a
document, talks to the model or touches the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

from osa.llm.envelopes import IntentEnvelope, QueryEnvelope

#: Hits shown as buttons; one more is fetched to know there are more than these.
MAX_OFFERED: Final = 5
SEARCH_LIMIT: Final = MAX_OFFERED + 1


class Action(StrEnum):
    BUSCAR = "buscar"
    LISTAR_DONOS = "listar_donos"
    LISTAR_DEPARTAMENTOS = "listar_departamentos"
    STATUS = "status"
    AJUDA = "ajuda"


@dataclass(frozen=True, slots=True)
class Search:
    query: QueryEnvelope


@dataclass(frozen=True, slots=True)
class ListOwners:
    pass


@dataclass(frozen=True, slots=True)
class ListDepartments:
    pass


@dataclass(frozen=True, slots=True)
class Status:
    pass


@dataclass(frozen=True, slots=True)
class Help:
    pass


Plan = Search | ListOwners | ListDepartments | Status | Help


def plan(envelope: IntentEnvelope) -> Plan:
    """The action the code runs for the envelope; a search with no filter at all is help.

    A "buscar" that names nothing (no owner, department, type, period or keyword) would list
    the most recent documents of the whole archive: the bot asks what to look for instead.
    """
    action = Action(envelope.acao)
    if action is Action.BUSCAR:
        query = envelope.consulta
        if not has_filters(query):
            return Help()
        return Search(query)
    if action is Action.LISTAR_DONOS:
        return ListOwners()
    if action is Action.LISTAR_DEPARTAMENTOS:
        return ListDepartments()
    if action is Action.STATUS:
        return Status()
    return Help()


def has_filters(query: QueryEnvelope) -> bool:
    return any(
        (
            query.dono,
            query.departamento,
            query.tipo_documental,
            query.periodo_inicio,
            query.periodo_fim,
            query.palavras_chave,
        )
    )


# --- delivery -----------------------------------------------------------------------------

Delivery = Literal["arquivo", "caminho", "recusado"]
MOTIVO_SENSIVEL: Final = "classe sensível: só o administrador recebe o caminho"
MOTIVO_GRANDE: Final = "acima do limite de envio do Telegram"


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    how: Delivery
    motivo: str | None


def delivery(*, sensitive: bool, size: int, limit_bytes: int, is_admin: bool) -> DeliveryDecision:
    """How a document reaches the person who asked (SPEC §5-B step 4).

    The sensitive class never goes as an attachment: its path, and only to the
    administrator; anyone else is refused. Above the send limit the path goes instead of
    the file. Everything else is sent.
    """
    if sensitive:
        if is_admin:
            return DeliveryDecision("caminho", MOTIVO_SENSIVEL)
        return DeliveryDecision("recusado", MOTIVO_SENSIVEL)
    if size > limit_bytes:
        return DeliveryDecision("caminho", MOTIVO_GRANDE)
    return DeliveryDecision("arquivo", None)


def network_path(unc: str | None, root: str, relative: str) -> str:
    """The path a user opens: the root's UNC plus the relative path, else the local path.

    ``unc`` is the canonical form the registry stores (``\\\\servidor\\acervo``); the relative
    path is POSIX and its separators become backslashes. Without a UNC, the path on this
    machine (the root plus the relative path) is all the bot can say.
    """
    parts = [part for part in relative.split("/") if part]
    if unc:
        return "\\".join([unc.rstrip("\\"), *parts])
    return "/".join([root.rstrip("/"), *parts])
