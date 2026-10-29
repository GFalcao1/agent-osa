"""Review by the reviewers' group (SPEC §5-D): owner, department or leave at the origin.

A case is posted with the document type, the short summary, the reason and buttons. A
case with a suggested department is decided by that department's reviewers (or the
administrator); without one, by any reviewer. The draft of a decision (owner and
department chosen so far) lives in memory; the decision itself goes through
``osa.courier.review`` and is audited with the actor. A repeated decision is ignored.
"""

from __future__ import annotations

from collections.abc import Sequence

from osa.courier.pipeline import Archived, ArchiveRefused, Reviewed
from osa.domain.document import ReviewState
from osa.domain.errors import DomainError
from osa.telegram import texts
from osa.telegram.auth import identify, may_decide
from osa.telegram.ports import (
    Actor,
    BotContext,
    Button,
    Callback,
    CaseInfo,
    Keyboard,
    OwnerInfo,
    ReviewDraft,
)

PREFIX = "rv:"
PAGE = 8


def post_case(
    ctx: BotContext,
    reviewed: Reviewed,
    *,
    declared_owner: OwnerInfo | None,
    fallback_chat_id: int,
) -> int:
    """Post the case to the reviewers' group (or, without one, to the operator's chat)."""
    chat_id = ctx.reviewers_chat_id if ctx.reviewers_chat_id is not None else fallback_chat_id
    case = ctx.courier.case(reviewed.case_id)
    draft = ReviewDraft(
        case_id=case.id,
        chat_id=chat_id,
        message_id=0,
        owner_id=declared_owner.id if declared_owner is not None else None,
        department_id=case.departamento_sugerido_id,
    )
    message_id = ctx.bot.send(chat_id, render(ctx, case, draft), keyboard(case, draft))
    draft.message_id = message_id
    with ctx.state.lock:
        ctx.state.drafts[case.id] = draft
    return message_id


def render(ctx: BotContext, case: CaseInfo, draft: ReviewDraft) -> str:
    document = ctx.courier.document(case.document_id)
    owner = ctx.courier.owner(draft.owner_id).nome_fantasia if draft.owner_id else "—"
    departments = {d.id: d.nome for d in ctx.courier.departments()}
    department = departments.get(draft.department_id or -1, "—")
    reviewers = ""
    if case.departamento_sugerido_id is not None:
        names = [a.nome for a in ctx.courier.users_of_department(case.departamento_sugerido_id)]
        reviewers = f"\nRevisores: {', '.join(names) if names else 'nenhum cadastrado'}"
    head = " · ".join(
        part
        for part in (document.tipo_documental, texts.short(document.resumo, texts.MAX_RESUMO))
        if part
    )
    return (
        f"Revisão {case.id}"
        + (f" — {head}" if head else "")
        + f"\nMotivo: {texts.describe_reason(case.motivo)}"
        + f"\nDono: {owner}\nDepartamento: {department}"
        + reviewers
    )


def keyboard(case: CaseInfo, draft: ReviewDraft) -> Keyboard:
    rows: list[list[Button]] = [
        [
            Button("Escolher dono", f"{PREFIX}{case.id}:own"),
            Button("Escolher departamento", f"{PREFIX}{case.id}:dep"),
        ]
    ]
    actions: list[Button] = []
    if draft.owner_id is not None and draft.department_id is not None:
        actions.append(Button("Arquivar", f"{PREFIX}{case.id}:go"))
    actions.append(Button("Deixar na origem", f"{PREFIX}{case.id}:orig"))
    rows.append(actions)
    return rows


def handle_review_callback(ctx: BotContext, cb: Callback) -> None:
    actor = identify(ctx, cb.from_id, cb.chat_id, kind="callback")
    if actor is None:
        return
    case_text, _, action = cb.data[len(PREFIX) :].partition(":")
    try:
        case = ctx.courier.case(int(case_text))
    except (ValueError, DomainError):
        ctx.bot.answer(cb.callback_id, texts.DIALOG_LOST)
        return
    if not may_decide(actor, case.departamento_sugerido_id):
        name = next(
            (d.nome for d in ctx.courier.departments() if d.id == case.departamento_sugerido_id),
            "outro departamento",
        )
        ctx.bot.answer(cb.callback_id, texts.NOT_YOUR_DEPARTMENT.format(departamento=name))
        return
    if case.estado != ReviewState.ABERTO.value:
        ctx.bot.answer(cb.callback_id, texts.ALREADY_DECIDED)
        return
    draft = _draft(ctx, case, cb)
    if action == "own" or action.startswith("op:"):
        page = int(action[3:]) if action.startswith("op:") else 0
        ctx.bot.answer(cb.callback_id)
        ctx.bot.edit(
            cb.chat_id, cb.message_id, render(ctx, case, draft), _owner_pages(ctx, case, page)
        )
    elif action == "dep":
        ctx.bot.answer(cb.callback_id)
        rows = [[Button(d.nome, f"{PREFIX}{case.id}:d:{d.id}")] for d in ctx.courier.departments()]
        rows.append([Button("Voltar", f"{PREFIX}{case.id}:m")])
        ctx.bot.edit(cb.chat_id, cb.message_id, render(ctx, case, draft), rows)
    elif action.startswith("o:"):
        draft.owner_id = int(action[2:])
        ctx.bot.answer(cb.callback_id)
        ctx.bot.edit(cb.chat_id, cb.message_id, render(ctx, case, draft), keyboard(case, draft))
    elif action.startswith("d:"):
        draft.department_id = int(action[2:])
        ctx.bot.answer(cb.callback_id)
        ctx.bot.edit(cb.chat_id, cb.message_id, render(ctx, case, draft), keyboard(case, draft))
    elif action == "go":
        _archive(ctx, actor, case, draft, cb)
    elif action == "orig":
        result = ctx.courier.leave_at_origin(case.id, actor=actor)
        _close(ctx, case)
        ctx.bot.answer(cb.callback_id)
        ctx.bot.edit(
            cb.chat_id,
            cb.message_id,
            render(ctx, case, draft)
            + f"\n\n{texts.describe_outcome(result, tipo=None, resumo=None)}"
            f" Decidido por {actor.nome}.",
        )
    else:  # "m" and anything unknown: back to the main keyboard
        ctx.bot.answer(cb.callback_id)
        ctx.bot.edit(cb.chat_id, cb.message_id, render(ctx, case, draft), keyboard(case, draft))


def _draft(ctx: BotContext, case: CaseInfo, cb: Callback) -> ReviewDraft:
    with ctx.state.lock:
        draft = ctx.state.drafts.get(case.id)
        if draft is None:
            # After a restart: the reviewer chooses again, starting from the suggestion.
            draft = ReviewDraft(
                case.id, cb.chat_id, cb.message_id, None, case.departamento_sugerido_id
            )
            ctx.state.drafts[case.id] = draft
        return draft


def _owner_pages(ctx: BotContext, case: CaseInfo, page: int) -> Keyboard:
    owners: Sequence[OwnerInfo] = ctx.courier.owners()
    start = max(page, 0) * PAGE
    chunk = owners[start : start + PAGE]
    rows = [[Button(o.nome_fantasia, f"{PREFIX}{case.id}:o:{o.id}")] for o in chunk]
    nav: list[Button] = []
    if start > 0:
        nav.append(Button("« anteriores", f"{PREFIX}{case.id}:op:{page - 1}"))
    if start + PAGE < len(owners):
        nav.append(Button("mais »", f"{PREFIX}{case.id}:op:{page + 1}"))
    nav.append(Button("Voltar", f"{PREFIX}{case.id}:m"))
    rows.append(nav)
    return rows


def _archive(
    ctx: BotContext, actor: Actor, case: CaseInfo, draft: ReviewDraft, cb: Callback
) -> None:
    if draft.owner_id is None or draft.department_id is None:
        ctx.bot.answer(cb.callback_id, "Escolha o dono e o departamento antes.")
        return
    try:
        outcome = ctx.courier.decide_case(
            case.id, owner_id=draft.owner_id, department_id=draft.department_id, actor=actor
        )
    except ArchiveRefused as error:
        ctx.bot.answer(cb.callback_id)
        ctx.bot.edit(
            cb.chat_id,
            cb.message_id,
            render(ctx, case, draft) + f"\n\nO servidor recusou: {error}. Nada foi movido.",
            keyboard(case, draft),
        )
        return
    except DomainError as error:
        ctx.bot.answer(cb.callback_id, str(error))
        return
    document = ctx.courier.document(case.document_id)
    ctx.bot.answer(cb.callback_id)
    if isinstance(outcome, Archived):
        _close(ctx, case)
    ctx.bot.edit(
        cb.chat_id,
        cb.message_id,
        render(ctx, case, draft)
        + "\n\n"
        + texts.describe_outcome(outcome, tipo=document.tipo_documental, resumo=None)
        + f" Decidido por {actor.nome}.",
    )


def _close(ctx: BotContext, case: CaseInfo) -> None:
    with ctx.state.lock:
        ctx.state.drafts.pop(case.id, None)
