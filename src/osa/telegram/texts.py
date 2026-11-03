"""The sentences the bot says (Portuguese, plain) and how an outcome is described.

No sentence ever quotes document text: outcomes show the document type, the model's short
summary, the reason code and the destination, as SPEC §5-A and TASK-108 allow.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Final

from osa.courier.pipeline import Archived, Duplicate, Failed, NotSupported, Outcome, Reviewed
from osa.courier.review import LeftAtOrigin

if TYPE_CHECKING:
    from osa.telegram.ports import HitInfo, Understood

MAX_RESUMO: Final = 160
MAX_MOTIVO: Final = 120

REASONS: Final = {
    "sem_evidencia_de_dono": "nenhum dono identificado",
    "dono_ambiguo": "mais de um dono no texto",
    "dono_divergente": "o texto aponta outro dono",
    "departamento_invalido": "departamento fora do template",
    "ano_ausente": "sem ano de referência",
    "envelope_invalido": "resposta do modelo inválida",
    "arquivamento_recusado": "o servidor de arquivos recusou o nome ou a pasta",
    "nao_informado": "o modelo pediu revisão",
    "departamentos_ambiguos": "dois departamentos plausíveis",
    "instrucao_embutida": "instruções embutidas no documento",
    "texto_insuficiente": "texto insuficiente",
}

ASK_OWNER: Final = (
    "Recebi o arquivo. De quem é este documento? Responda com o nome ou o CNPJ do dono."
)
SENSITIVE_REFUSED: Final = (
    "Este arquivo é um certificado ou chave e não pode passar pelo Telegram. "
    "Coloque-o diretamente na pasta do servidor; nada foi baixado."
)
TOO_BIG: Final = (
    "O arquivo passa do limite de download do Telegram (20 MB). "
    "Coloque-o diretamente na pasta do servidor."
)
NO_OWNER_MATCH: Final = "Não encontrei esse dono no cadastro. Escolha uma opção:"
OWNER_REQUEST_SENT: Final = (
    "Pedi ao administrador para cadastrar o dono «{nome}». "
    "Assim que ele confirmar, o arquivo segue."
)
OWNER_REQUEST_NO_ADMIN: Final = (
    "Não há administrador cadastrado para confirmar um dono novo. Fale com o responsável."
)
OWNER_REQUEST_REFUSED: Final = (
    "O administrador não cadastrou o dono «{nome}». O arquivo não foi arquivado."
)
OWNER_CREATED: Final = "Dono «{nome}» cadastrado com a estrutura padrão."
UPLOAD_CANCELLED: Final = "Envio cancelado. Nada foi arquivado."
DOWNLOAD_FAILED: Final = "Não consegui baixar o arquivo do Telegram. Envie de novo."
NOT_YOURS: Final = "Esse botão não é seu."
ONLY_ADMIN: Final = "Só o administrador pode fazer isso."
ONLY_REVIEWER: Final = "Só revisores e o administrador usam este comando."
NOT_YOUR_DEPARTMENT: Final = "Este caso é dos revisores de {departamento}."
ALREADY_DECIDED: Final = "Este caso já foi decidido."
DIALOG_LOST: Final = "Esse pedido não está mais em memória (o bot reiniciou). Peça de novo."
REQUEST_ANSWERED: Final = "Este pedido já foi respondido, ou o bot reiniciou."
ONLY_REVIEWERS_DECIDE: Final = "Só revisores e o administrador decidem uma revisão."
PREVIOUS_UPLOAD_DROPPED: Final = (
    "O arquivo anterior, que esperava o dono, foi descartado; envie-o de novo se precisar."
)
OWNER_NAME_INVALID: Final = "Esse nome não serve para um dono ({erro}). Escreva outro nome."
OWNER_TREE_FAILED: Final = (
    "Dono «{nome}» cadastrado, mas as pastas dele não foram criadas: {erro}. "
    "O administrador precisa olhar o servidor."
)
UPLOAD_FAILED: Final = "Não consegui guardar o arquivo para processar. Envie de novo."
CONFIG_ERROR: Final = (
    "O cadastro impede a classificação: {erro}. O administrador precisa corrigir no painel."
)


HELP: Final = (
    "O que você pode me pedir:\n"
    "- Guardar: envie o documento como arquivo e diga de quem ele é na legenda.\n"
    "- Achar: escreva o que procura, por exemplo «manda o contrato da Alfa de 2025» ou "
    "«boletos de março da Beta». Eu mostro até 5 resultados para você escolher.\n"
    "- «quais donos existem?» e «quais departamentos existem?» listam o cadastro.\n"
    "- /pendentes mostra as revisões abertas (revisores); /status e /desfazer são do "
    "administrador."
)
ASK_WHAT: Final = (
    "Não entendi o que procurar. Diga o tipo de documento, o dono, o departamento ou o "
    "período, por exemplo «contrato da Alfa de 2025»."
)
QUERY_TOO_LONG: Final = "Mensagem longa demais para uma busca. Escreva só o que procura."
MODEL_UNAVAILABLE: Final = (
    "Não consegui entender o pedido agora (o modelo não respondeu). Tente de novo."
)
QUERY_FAILED: Final = "Não consegui fazer a busca: {erro}"
SEND_CANCELLED: Final = "Tudo bem, nada foi enviado."
SEND_FAILED: Final = "Não consegui enviar o arquivo pelo Telegram. Tente de novo."
SEND_REFUSED: Final = "Não enviado: {motivo}."
SEND_PATH: Final = "O arquivo não vai pelo Telegram ({motivo}). Abra no servidor:\n{caminho}"
OFFER_LOST: Final = "Essa busca não está mais em memória (o bot reiniciou). Peça de novo."
NOT_IN_OFFER: Final = "Esse documento não está nesta busca. Peça de novo."
MAX_LISTED_NAMES: Final = 50


def describe_filters(filtros: Understood) -> str:
    """The filters as the registry understood them, in one line; never a guess."""
    parts: list[str] = []
    if filtros.dono:
        parts.append(f"dono {filtros.dono}")
    if filtros.dono_desconhecido:
        parts.append(f"dono «{short(filtros.dono_desconhecido, 40)}» (não cadastrado)")
    if filtros.departamento:
        parts.append(f"departamento {filtros.departamento}")
    if filtros.departamento_desconhecido:
        parts.append(
            f"departamento «{short(filtros.departamento_desconhecido, 40)}» (fora do template)"
        )
    if filtros.tipo_documental:
        parts.append(f"tipo {short(filtros.tipo_documental, 40)}")
    if filtros.periodo_inicio or filtros.periodo_fim:
        parts.append(f"período {_period(filtros.periodo_inicio, filtros.periodo_fim)}")
    if filtros.palavras_chave:
        parts.append("palavras " + ", ".join(short(w, 30) for w in filtros.palavras_chave))
    return " · ".join(parts) if parts else "nenhum filtro"


def _period(start: date | None, end: date | None) -> str:
    if start and end:
        return f"de {start:%d/%m/%Y} a {end:%d/%m/%Y}"
    if start:
        return f"a partir de {start:%d/%m/%Y}"
    assert end is not None
    return f"até {end:%d/%m/%Y}"


def hit_label(hit: HitInfo, limit: int = 60) -> str:
    """A button: the file name, the owner and the year; the sensitive class is flagged."""
    extra = " · ".join(str(part) for part in (hit.dono, hit.ano) if part)
    label = f"{hit.nome} · {extra}" if extra else hit.nome
    if hit.sensivel:
        label = f"[sensível] {label}"
    return short(label, limit)


def short(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def describe_reason(motivo: str | None) -> str:
    """Known codes become phrases; anything else is model text, shown short."""
    if not motivo:
        return "sem motivo informado"
    parts = [REASONS.get(part, short(part, MAX_MOTIVO)) for part in motivo.split("; ")]
    return "; ".join(parts)


def describe_outcome(
    outcome: Outcome | LeftAtOrigin,
    *,
    tipo: str | None,
    resumo: str | None,
    effects_enabled: bool = True,
) -> str:
    """One reply for the operator: what happened and where the file is."""
    head = " · ".join(part for part in (tipo, short(resumo, MAX_RESUMO)) if part)
    where = "O arquivo ficou onde estava." if effects_enabled else "Nada foi gravado no servidor."
    match outcome:
        case Archived(simulated=True, destino=destino):
            return f"Simulado (efeitos de arquivo desligados): iria para {destino}." + (
                f"\n{head}" if head else ""
            )
        case Archived(destino=destino):
            return f"Arquivado em {destino}." + (f"\n{head}" if head else "")
        case Reviewed(case_id=case_id, motivo=motivo):
            return (
                f"Foi para revisão (caso {case_id}): {describe_reason(motivo)}. "
                "Os revisores foram avisados." + (f"\n{head}" if head else "")
            )
        case Duplicate(estado=estado, caminho=caminho):
            where = f" em {caminho}" if caminho else ""
            return f"Este arquivo já está no acervo ({estado}){where}. Nada foi feito."
        case NotSupported(motivo=motivo, estado=estado):
            return f"Não processado ({estado}): {motivo}. {where}"
        case Failed(motivo=motivo):
            tail = (
                "O arquivo ficou na quarentena; o administrador pode reprocessar."
                if effects_enabled
                else "Nada foi gravado no servidor."
            )
            return f"Falhou: {motivo}. {tail}"
        case LeftAtOrigin(case_id=case_id):
            return f"Caso {case_id}: o arquivo ficou na origem."
    return str(outcome)
