"""Commands: ``/status`` and ``/desfazer`` for the administrator, ``/pendentes`` for reviewers."""

from __future__ import annotations

from osa.domain.errors import DomainError
from osa.telegram import texts
from osa.telegram.auth import identify
from osa.telegram.ports import Actor, BotContext, CaseInfo, IncomingCommand

MAX_LISTED = 30

STATE_LABELS = {
    "DETECTADO": "detectados",
    "ESTAVEL": "estáveis",
    "EXTRAIDO": "extraídos",
    "CLASSIFICADO": "classificados",
    "ARQUIVADO": "arquivados",
    "EM_REVISAO": "em revisão",
    "NA_ORIGEM": "na origem",
    "NAO_SUPORTADO": "não suportados",
    "FALHOU": "falharam",
}


def handle_command(ctx: BotContext, cmd: IncomingCommand) -> None:
    actor = identify(ctx, cmd.from_id, cmd.chat_id, kind=f"command:{cmd.name}")
    if actor is None:
        return
    if cmd.name == "status":
        _status(ctx, actor, cmd)
    elif cmd.name == "desfazer":
        _undo(ctx, actor, cmd)
    elif cmd.name == "pendentes":
        _pending(ctx, actor, cmd)
    elif cmd.name == "start":
        ctx.bot.send(
            cmd.chat_id,
            f"Olá, {actor.nome}. Envie um documento como arquivo e diga de quem ele é na legenda.",
        )


def _status(ctx: BotContext, actor: Actor, cmd: IncomingCommand) -> None:
    if not actor.is_admin:
        ctx.bot.send(cmd.chat_id, texts.ONLY_ADMIN)
        return
    info = ctx.courier.status()
    counts = ", ".join(
        f"{info.documentos.get(state, 0)} {label}" for state, label in STATE_LABELS.items()
    )
    roots = "\n".join(
        f"- {root.caminho}: "
        + (
            f"{root.livre_bytes / 2**30:.1f} GB livres"
            if root.livre_bytes is not None
            else "sem medida"
        )
        for root in info.raizes
    )
    ctx.bot.send(
        cmd.chat_id,
        f"Fila: {counts}.\n"
        f"Revisões abertas: {info.revisoes_abertas}.\n"
        f"Modelo: {info.modelo}" + (f" ({info.digest[:12]}…)" if info.digest else "") + ".\n"
        f"Efeitos de arquivo: {'ligados' if info.efeitos else 'desligados'}.\n"
        "Último scan: ainda não há scan nesta versão.\n"
        f"Raízes:\n{roots or '- nenhuma ativa'}",
    )


def _undo(ctx: BotContext, actor: Actor, cmd: IncomingCommand) -> None:
    if not actor.is_admin:
        ctx.bot.send(cmd.chat_id, texts.ONLY_ADMIN)
        return
    if len(cmd.args) != 1 or not cmd.args[0].isdigit():
        ctx.bot.send(cmd.chat_id, "Use: /desfazer <número da operação>")
        return
    try:
        result = ctx.courier.undo(int(cmd.args[0]), actor=actor)
    except DomainError as error:
        ctx.bot.send(cmd.chat_id, f"Não desfeito: {error}")
        return
    if result.simulated:
        ctx.bot.send(
            cmd.chat_id,
            f"Simulado (efeitos desligados): o arquivo voltaria para {result.destino}.",
        )
    else:
        ctx.bot.send(
            cmd.chat_id,
            f"Desfeito: documento {result.document_id} de volta em {result.destino} "
            f"(operação {result.undo_operation_id}).",
        )


def _pending(ctx: BotContext, actor: Actor, cmd: IncomingCommand) -> None:
    if not actor.is_reviewer:
        ctx.bot.send(cmd.chat_id, texts.ONLY_REVIEWER)
        return
    cases = [c for c in ctx.courier.open_cases() if _visible(actor, c)]
    ctx.bot.send(cmd.chat_id, pending_text(ctx, cases))


def _visible(actor: Actor, case: CaseInfo) -> bool:
    if actor.is_admin or case.departamento_sugerido_id is None:
        return True
    return case.departamento_sugerido_id == actor.departamento_id


def pending_text(ctx: BotContext, cases: list[CaseInfo]) -> str:
    """The list the reminder and ``/pendentes`` share; never document text."""
    if not cases:
        return "Nenhuma revisão pendente."
    departments = {d.id: d.nome for d in ctx.courier.departments()}
    lines = []
    for case in cases[:MAX_LISTED]:
        document = ctx.courier.document(case.document_id)
        department = departments.get(case.departamento_sugerido_id or -1, "sem departamento")
        when = f", desde {case.criado_em:%d/%m}" if case.criado_em else ""
        lines.append(
            f"- caso {case.id}: {document.tipo_documental or 'documento'} · "
            f"{texts.describe_reason(case.motivo)} · {department}{when}"
        )
    if len(cases) > MAX_LISTED:
        lines.append(f"… e mais {len(cases) - MAX_LISTED}.")
    return f"Revisões pendentes ({len(cases)}):\n" + "\n".join(lines)
