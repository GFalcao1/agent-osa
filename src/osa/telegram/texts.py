"""The sentences the bot says (Portuguese, plain) and how an outcome is described.

No sentence ever quotes document text: outcomes show the document type, the model's short
summary, the reason code and the destination, as SPEC §5-A and TASK-108 allow.
"""

from __future__ import annotations

from typing import Final

from osa.courier.pipeline import Archived, Duplicate, Failed, NotSupported, Outcome, Reviewed
from osa.courier.review import LeftAtOrigin

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
CONFIG_ERROR: Final = (
    "O cadastro impede a classificação: {erro}. O administrador precisa corrigir no painel."
)


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
    outcome: Outcome | LeftAtOrigin, *, tipo: str | None, resumo: str | None
) -> str:
    """One reply for the operator: what happened and where the file is."""
    head = " · ".join(part for part in (tipo, short(resumo, MAX_RESUMO)) if part)
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
            return f"Não processado ({estado}): {motivo}. O arquivo ficou onde estava."
        case Failed(motivo=motivo):
            return (
                f"Falhou: {motivo}. O arquivo ficou na quarentena; "
                "o administrador pode reprocessar."
            )
        case LeftAtOrigin(case_id=case_id):
            return f"Caso {case_id}: o arquivo ficou na origem."
    return str(outcome)
