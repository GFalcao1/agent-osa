"""External provider policy (spec §8, ADR-0002): the query envelope only, owners masked.

Only the text of a Telegram request can leave the machine, and only after every registered
owner name, alias and CNPJ in it was replaced by ``dono:<id>`` (any other valid CNPJ becomes
``[cnpj]``). A second check, written differently from the masker, scans the WHOLE payload
(system prompt, user message and schema) and refuses to send if a registered name or a valid
CNPJ is still readable. Document content never reaches this module: ``classify`` refuses
before touching its argument, and nothing here imports the extraction layer.

No vendor is bundled: a deployment supplies an ``ExternalTransport``. This module is only
imported when the switch is on (``osa.llm.provider.build_query_provider``).
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Protocol

from osa.domain.owner import is_valid_cnpj
from osa.llm import prompts
from osa.llm.envelopes import (
    DepartmentInput,
    QueryEnvelope,
    department_specs,
    parse_envelope,
    query_envelope,
    schema_hash,
    schema_json,
    wire_schema,
)
from osa.llm.provider import LLMError, QueryTooLong

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from osa.extract.model import ExtractedContent

ACTION_INTERPRET = "llm.external.interpret"
ACTION_CLASSIFY = "llm.external.classify"
OWNER_TOKEN = "dono:{}"
CNPJ_TOKEN = "[cnpj]"
_TOKENS = re.compile(r"dono:\d+|\[cnpj\]")
_OWNER_REPLY = re.compile(r"dono:(\d+)")
_MIN_COMPACT_LEN = 6  # shorter names are only checked at word boundaries (too many false hits)
# A name ends where letters end: a digit next to it is a boundary ("alfa2025" shows "alfa").
# Masking "Alfa3D" too is the side preferred to a leak.
_LEFT, _RIGHT = r"(?<![a-z])", r"(?![a-z])"

# Runs of ASCII digits (after ``ascii_digits``) joined by any non-alphanumeric separators, up
# to ``_MAX_GAP`` characters each (spaces of any width, punctuation, zero-width, symbols).
_MAX_GAP = 16
_CNPJ_RUN_MASKER = re.compile(rf"[0-9](?:[\W_]{{0,{_MAX_GAP}}}[0-9])*")
# A second, older scan: up to 3 of ANY non-digit (letters included) between digits, so
# "11a222b333..." is caught too. Both scans block; neither masks.
_CNPJ_RUN_SCAN = re.compile(r"[0-9](?:[^0-9]{0,3}[0-9])*")
_CNPJ_LEN = 14

AuditResult = Literal["tentativa", "ok", "failed", "denied"]


class ExternalTransport(Protocol):
    """What a deployment provides to reach an external model. No vendor ships in the product."""

    retencao_zero: bool  # attested by whoever installs it (spec §8: zero retention required)
    rotulo: str  # a short label for the audit trail

    def complete_json(
        self, *, system: str, user: str, schema: Mapping[str, Any], timeout_s: float
    ) -> str:
        """Send the masked request; return the raw JSON text the model produced."""
        ...


@dataclass(frozen=True, slots=True)
class OwnerNames:
    """One registered owner as the masker needs it (built by the caller from the registry)."""

    id: int
    rotulo: str  # canonical name handed back when the model returns ``dono:<id>``
    nomes: tuple[str, ...]  # fantasy name, legal name and every alias
    cnpjs: tuple[str, ...] = ()  # 14 digits, no mask


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One audit fact about an external call. Never text: counts, hashes and labels."""

    acao: str
    resultado: AuditResult
    detalhes: Mapping[str, object]


AuditSink = Callable[[AuditRecord], None]


class ExternalDisabled(LLMError):
    """The external provider is off, has no transport or the transport lacks zero retention."""


class ExternalClassifyRefused(LLMError):
    """Document content never goes to an external provider, under any configuration."""


class ExternalAuditFailed(LLMError):
    """The audit trail could not be written: no call is made (or its answer is not used)."""


class ExternalLeakBlocked(LLMError):
    """A registered name or a valid CNPJ was still readable in the payload; nothing was sent."""


class ExternalCallFailed(LLMError):
    """The transport failed; the message holds the exception type only."""


# --- masking ------------------------------------------------------------------------


def _digit_value(char: str) -> int | None:
    """0..9 for any character Unicode gives that numeric value (Nd of any script, circled,
    superscript, fullwidth, CJK numerals); None otherwise."""
    value = unicodedata.digit(char, None)
    if value is not None:
        return value
    number = unicodedata.numeric(char, None)
    if number is not None and number in range(10):
        return int(number)
    return None


def ascii_digits(text: str) -> str:
    """NFKC, then every character with a numeric value 0..9 as its ASCII digit.

    Same length as the NFKC text, so positions found in it are positions in the result.
    """
    out = []
    for char in unicodedata.normalize("NFKC", text):
        value = _digit_value(char)
        out.append(char if value is None else str(value))
    return "".join(out)


def _skeleton_has_cnpj(text: str) -> bool:
    """Fail-closed: digits only (numeric value 0..9), letters break a run, the rest is ignored;
    True if any 14-digit window of a run is a valid CNPJ."""
    runs: list[str] = []
    current: list[str] = []
    for char in unicodedata.normalize("NFKC", text):
        value = _digit_value(char)
        if value is not None:
            current.append(str(value))
        elif char.isalpha():
            runs.append("".join(current))
            current = []
    runs.append("".join(current))
    return any(
        is_valid_cnpj(run[i : i + _CNPJ_LEN])
        for run in runs
        for i in range(len(run) - _CNPJ_LEN + 1)
    )


def _cnpj_windows(text: str, runs: re.Pattern[str]) -> list[tuple[int, int, str]]:
    """``(start, end, digits)`` of every valid CNPJ in ``text``, glued to other digits or not.

    A 14-digit window slides over the digits of each run; a valid window is taken and the
    search goes on after it. ``text`` must already have ASCII digits.
    """
    found: list[tuple[int, int, str]] = []
    for match in runs.finditer(text):
        positions = [match.start() + k for k, c in enumerate(match.group(0)) if c.isdigit()]
        digits = "".join(text[k] for k in positions)
        i = 0
        while i + _CNPJ_LEN <= len(digits):
            window = digits[i : i + _CNPJ_LEN]
            if is_valid_cnpj(window):
                found.append((positions[i], positions[i + _CNPJ_LEN - 1] + 1, window))
                i += _CNPJ_LEN
            else:
                i += 1
    return found


def _fold_char(char: str) -> str:
    folded = unicodedata.normalize("NFKD", unicodedata.normalize("NFKD", char).casefold())
    return "".join(c for c in folded if not unicodedata.combining(c))


def _fold(text: str) -> tuple[str, str, list[int]]:
    """``(nfc_text, normalized, index)`` where ``index[k]`` is the NFC position of char ``k``.

    Letters and digits fold to ASCII; every other run collapses to one space. Folding char by
    char keeps the position map exact (ligatures and decomposed accents included).
    """
    nfc = unicodedata.normalize("NFC", text)
    out: list[str] = []
    index: list[int] = []
    for position, char in enumerate(nfc):
        for piece in _fold_char(char):
            kept = piece if "0" <= piece <= "9" or "a" <= piece <= "z" else " "
            if kept == " " and out and out[-1] == " ":
                continue
            out.append(kept)
            index.append(position)
    return nfc, "".join(out), index


def _name_keys(owners: Sequence[OwnerNames]) -> dict[str, int]:
    keys: dict[str, int] = {}
    for owner in owners:
        for name in (*owner.nomes, owner.rotulo):
            key = _fold(name)[1].strip()
            if key:
                keys.setdefault(key, owner.id)
    return keys


def mask_owner_names(text: str, owners: Sequence[OwnerNames]) -> tuple[str, int]:
    """Replace registered names and aliases with ``dono:<id>`` and CNPJs with a token.

    Names match at letter boundaries (a digit next to a name does not hide it) after folding
    case, accents, ligatures and punctuation, the longest first, left to right, in one pass
    (the token is never rescanned). A token followed by a letter or digit gets a space, so
    "alfa2025" becomes "dono:1 2025" and never reads as owner 12025. Returns the new text and
    how many replacements were made.
    """
    keys = _name_keys(owners)
    text = ascii_digits(text)  # "１１２…" and "١١٢…" are digits too
    result, count = text, 0
    if keys:
        ordered = sorted(keys, key=len, reverse=True)
        pattern = re.compile(
            _LEFT + "(" + "|".join(re.escape(key) for key in ordered) + ")" + _RIGHT
        )
        nfc, normalized, index = _fold(text)
        spans = [
            (index[m.start()], index[m.end() - 1] + 1, keys[m.group(1)])
            for m in pattern.finditer(normalized)
        ]
        for start, end, owner_id in reversed(spans):
            token = OWNER_TOKEN.format(owner_id)
            if end < len(nfc) and nfc[end].isalnum():
                token += " "
            nfc = nfc[:start] + token + nfc[end:]
        result, count = nfc, len(spans)
    by_cnpj = {digits: owner.id for owner in owners for digits in owner.cnpjs}
    for start, end, digits in reversed(_cnpj_windows(result, _CNPJ_RUN_MASKER)):
        token = OWNER_TOKEN.format(by_cnpj[digits]) if digits in by_cnpj else CNPJ_TOKEN
        if end < len(result) and result[end].isalnum():
            token += " "
        result = result[:start] + token + result[end:]
        count += 1
    return result, count


def find_leak(payload: str, owners: Sequence[OwnerNames]) -> str | None:
    """``"nome"`` or ``"cnpj"`` if the payload still shows a registered name or a valid CNPJ.

    Deliberately not the masker's code: names are searched one by one at letter boundaries and,
    for names of at least six letters, in the compact form with every separator removed (which
    catches "AlfaComércioLtda"); CNPJs are searched in the digit skeleton of the whole payload
    and with up to 3 of any non-digit between digits, wider than the masker.
    Tokens the masker wrote are removed first. A false positive blocks the call: preferred.
    """
    raw = ascii_digits(payload)
    text = _TOKENS.sub(" ", raw)  # for names only: the tokens are not names
    normalized = _fold(text)[1]
    compact = normalized.replace(" ", "")
    for key in _name_keys(owners):
        if re.search(_LEFT + re.escape(key) + _RIGHT, normalized):
            return "nome"
        squeezed = key.replace(" ", "")
        if len(squeezed) >= _MIN_COMPACT_LEN and squeezed in compact:
            return "nome"
    # Structural and fail-closed: any valid CNPJ in the digit skeleton BLOCKS the call (never
    # masks). A long ordinary number can be a false positive; the caller then uses the local
    # provider, which is safe (ADR-0012).
    # On the payload as sent, nothing removed: a "dono:" prefix must not hide digits. The
    # masker's own tokens never glue to a neighbouring digit ("dono:1 2025").
    if _skeleton_has_cnpj(raw) or _cnpj_windows(raw, _CNPJ_RUN_SCAN):
        return "cnpj"
    return None


# --- the provider -------------------------------------------------------------------


class ExternalQueryProvider:
    """``LLMProvider`` for the query envelope only; ``classify`` always refuses."""

    def __init__(
        self,
        *,
        enabled: bool,
        transport: ExternalTransport | None,
        owners_source: Callable[[], Sequence[OwnerNames]],
        audit: AuditSink,
        actor: str = "system",
        timeout_s: float = 60.0,
        today: Callable[[], date] = date.today,
        nonce_factory: Callable[[], str] = prompts.default_nonce,
    ) -> None:
        self._enabled = enabled
        self._transport = transport
        self._owners_source = owners_source
        self._audit = audit
        self._actor = actor
        self._timeout_s = timeout_s
        self._today = today
        self._nonce_factory = nonce_factory

    def classify(
        self, content: ExtractedContent, departments: Sequence[DepartmentInput]
    ) -> NoReturn:
        """Refuse before looking at ``content``; the refusal is audited if the trail works."""
        del content, departments
        self._note(AuditRecord(ACTION_CLASSIFY, "denied", {"motivo": "classe_documento"}))
        raise ExternalClassifyRefused("conteúdo de documento não sai da máquina")

    def interpret(self, text: str, departments: Sequence[DepartmentInput]) -> QueryEnvelope:
        transport = self._transport
        if not self._enabled or transport is None:
            raise ExternalDisabled("provedor externo desligado")
        if not transport.retencao_zero:
            raise ExternalDisabled("transporte externo sem retenção zero")
        names = department_specs(departments)
        # The Telegram limit applies to what the user wrote: masking lengthens the text.
        if len(text) > prompts.MAX_QUERY_CHARS:
            raise QueryTooLong("pedido longo demais")
        owners = tuple(self._owners_source())
        masked, replaced = mask_owner_names(prompts.sanitize_document_text(text), owners)
        rendered = prompts.render_query(
            masked, names, today=self._today(), nonce_factory=self._nonce_factory, max_chars=None
        )
        model = query_envelope(names)
        schema = wire_schema(model)
        payload = "\n".join((rendered.system, rendered.user, schema_json(schema)))
        leak = find_leak(payload, owners)
        if leak is not None:
            self._note(AuditRecord(ACTION_INTERPRET, "denied", {"motivo": f"vazamento_{leak}"}))
            raise ExternalLeakBlocked("a consulta ainda continha dado cadastrado; nada foi enviado")

        details: dict[str, object] = {
            "transporte": transport.rotulo,
            "ator": self._actor,
            "donos_substituidos": replaced,
            "bytes": len(payload.encode("utf-8")),
            "prompt_hash": prompts.prompt_hash("query"),
            "schema_hash": schema_hash(schema),
        }
        # The attempt is on record before any byte leaves; a trail that cannot be written
        # means no call at all.
        self._write(AuditRecord(ACTION_INTERPRET, "tentativa", details))
        started = time.perf_counter()
        envelope: QueryEnvelope | None = None
        failure: LLMError | None = None
        phase: str | None = None
        error_type: str | None = None
        try:
            raw = transport.complete_json(
                system=rendered.system,
                user=rendered.user,
                schema=schema,
                timeout_s=self._timeout_s,
            )
        except Exception as error:
            phase, error_type = "transporte", type(error).__name__
            failure = ExternalCallFailed(f"falha no provedor externo ({error_type})")
        else:
            try:
                envelope = self._resolve(parse_envelope(model, raw), owners)
            except LLMError as error:
                phase, failure = "envelope", error
        result: dict[str, object] = {
            "duracao_ms": round((time.perf_counter() - started) * 1000),
            "falha": phase,
        }
        if error_type is not None:
            result["erro"] = error_type
        # Written only after the network answered (or failed): never "ok" ahead of time.
        self._write(AuditRecord(ACTION_INTERPRET, "failed" if failure else "ok", result))
        if failure is not None:
            raise failure
        assert envelope is not None
        return envelope

    def _write(self, record: AuditRecord) -> None:
        try:
            self._audit(record)
        except Exception:
            raise ExternalAuditFailed(
                "a auditoria da chamada externa não pôde ser gravada"
            ) from None

    def _note(self, record: AuditRecord) -> None:
        try:
            self._audit(record)
        except Exception:
            pass  # a block is a block even if it could not be written down

    @staticmethod
    def _resolve(envelope: QueryEnvelope, owners: Sequence[OwnerNames]) -> QueryEnvelope:
        """``dono:<id>`` back to the canonical name, locally; unknown ids are dropped."""
        labels = {owner.id: owner.rotulo for owner in owners}

        def label(value: str) -> str | None:
            match = _OWNER_REPLY.fullmatch(value.strip())
            if match is None:
                return value  # a name the user wrote themselves: their own word
            return labels.get(int(match.group(1)))

        owner = envelope.dono
        words: list[str] = []
        for word in envelope.palavras_chave:
            resolved = label(word)
            if resolved is not None:
                words.append(resolved)
        return envelope.model_copy(
            update={
                "dono": None if owner is None else label(owner),
                "palavras_chave": words,
            }
        )


def db_audit_sink(session_factory: Callable[[], Session], actor: str) -> AuditSink:
    """An ``AuditSink`` that commits each record on its own session (survives later rollbacks).

    The audit table has no "pending" value, so an unconfirmed ``tentativa`` is stored as
    ``failed`` with ``fase="tentativa"``: it can never read as a delivered call. The later
    ``ok`` or ``failed`` event settles it.
    """
    from osa.db.audit import RESULT_DENIED, RESULT_FAILED, RESULT_OK, record_event

    def sink(record: AuditRecord) -> None:
        stored = {"tentativa": RESULT_FAILED, "ok": RESULT_OK, "failed": RESULT_FAILED}.get(
            record.resultado, RESULT_DENIED
        )
        details: dict[str, Any] = json.loads(json.dumps(dict(record.detalhes), default=str))
        if record.resultado == "tentativa":
            details.update({"fase": "tentativa", "desfecho": "pendente"})
        with session_factory() as session:
            record_event(
                session,
                ator=actor,
                acao=record.acao,
                alvo_tipo="llm_externo",
                resultado=stored,
                detalhes=details,
            )
            session.commit()

    return sink
