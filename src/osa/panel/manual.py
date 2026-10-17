"""Facts behind the built-in manual (``/manual``): chapter ids and the on-screen messages.

The manual itself is hand-written in ``templates/manual.html``. What lives here is the part
that must not drift from the code: the chapter ids (links from every page point at them) and
the exact refusal texts the panel shows, each with a plain-language reading. The tests check
every message against the code that raises it (``osa.admin.services``, ``osa.domain.owner``
and the panel routes), so renaming a message there breaks a test here instead of silently
leaving the manual out of date. Texts that already exist as constants are imported, not copied.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from osa.admin.services import MAX_FOLDER_NAME
from osa.panel.routes.common import NOTICES

Kind = Literal["erro", "aviso"]


@dataclass(frozen=True)
class Chapter:
    id: str
    title: str


@dataclass(frozen=True)
class Problem:
    """One text the panel can show, what it means and what to do (plain language)."""

    message: str
    meaning: str
    action: str
    kind: Kind = "erro"
    anchor: str = ""


CHAPTERS: Final = (
    Chapter("comece", "Comece por aqui"),
    Chapter("raizes", "Raízes e pastas monitoradas"),
    Chapter("template", "Template de departamentos"),
    Chapter("donos", "Donos"),
    Chapter("usuarios", "Usuários"),
    Chapter("modelo", "Modelo e provedor externo"),
    Chapter("palavras", "Palavras que você vai encontrar"),
    Chapter("problemas", "Problemas comuns"),
    Chapter("seguranca", "Segurança em linguagem simples"),
    Chapter("ajuda", "Quando chamar o responsável técnico"),
)

# Chapters that describe one page of the panel: each has the four standard parts.
SECTION_CHAPTERS: Final = ("raizes", "template", "donos", "usuarios", "modelo")
SECTION_PARTS: Final = ("para-que", "passos", "dicas", "erros")

# Label -> the page whose form shows it (the tests check the template
# and the rendered page with data).
SCREEN_LABELS: Final = {
    "Cadastrar raiz": "/roots",
    "Nova pasta monitorada": "/roots",
    "Adicionar departamento": "/departments",
    "Subpasta por ano": "/departments",
    "Subir": "/departments",
    "Descer": "/departments",
    "Desativar": "/departments",
    "Ativar": "/departments",
    "Cadastrar dono": "/owners",
    "Nome fantasia": "/owners",
    "Razão social": "/owners",
    "Adicionar alias": "/owners/{owner}",
    "Remover": "/owners/{owner}",
    "Cadastrar usuário": "/users",
    "Telegram ID": "/users",
    "Nome do modelo": "/model",
    "Ligar provedor externo": "/model",
}

# Shown by the template/app, not raised by a service; the tests look for them there.
BLOCKED_TITLE: Final = "Cadastro bloqueado"
BLOCKED_TEXT: Final = "Nenhum administrador cadastrado."

# What the browser shows after a save from a page the panel no longer knows (the CSRF check
# raises HTTPException(403, "forbidden"), which FastAPI renders as this JSON body).
STALE_PAGE_TEXT: Final = '{"detail":"forbidden"}'

_NAME_REQUIRED = "nome é obrigatório"
_FOLDER_RETRY = "Escolha outro nome. Evite sinais; letras com acento, números e espaços podem."
_OPEN_AGAIN = "Abra a seção de novo pelo menu do painel (clique no nome dela) e repita."
_NOT_FOUND = (
    "O item não existe mais nesse endereço. Costuma ser uma página antiga ou aberta em duas abas."
)

# Folder-name rules are shared by departments and owners.
FOLDER_NAME_PROBLEMS: Final = (
    Problem(
        "nome de pasta não pode ser vazio",
        "O campo da pasta ficou vazio, ou o nome só tinha caracteres que não podem ser usados.",
        "Digite um nome para a pasta. " + _FOLDER_RETRY,
    ),
    Problem(
        f"nome de pasta excede {MAX_FOLDER_NAME} caracteres",
        f"O nome da pasta passou de {MAX_FOLDER_NAME} letras.",
        "Use um nome mais curto.",
    ),
    Problem(
        "nome de pasta contém caractere proibido",
        'Há um sinal que não pode fazer parte do nome de uma pasta: < > : " | ? * / ou \\.',
        "Tire esses sinais. " + _FOLDER_RETRY,
    ),
    Problem(
        "nome de pasta não pode começar ou terminar com ponto ou espaço",
        "O nome começa ou termina com um ponto ou com um espaço. "
        "O servidor de arquivos não aceita.",
        "Apague o ponto ou o espaço das pontas.",
    ),
    Problem(
        "nome de pasta é reservado",
        "O nome é reservado pelo servidor de arquivos "
        "(por exemplo CON, PRN, AUX, NUL, COM1 ou LPT1).",
        "Escolha outro nome.",
    ),
)

_FOLDERS_NOT_RENAMED = Problem(
    NOTICES["pastas"],
    "Você mudou o nome da pasta no painel. As pastas que já existiam no servidor continuam "
    "com o nome antigo. Não é um erro: o cadastro foi salvo.",
    "Se quiser que o servidor acompanhe, combine com o responsável técnico antes de renomear "
    "as pastas. Se não, deixe como está.",
    kind="aviso",
)

PROBLEMS: Final[dict[str, tuple[Problem, ...]]] = {
    "raizes": (
        Problem(
            "raiz deve ser um caminho absoluto",
            "O endereço da pasta precisa começar com uma barra (/). Endereços incompletos "
            "ou vazios não servem.",
            "Digite o endereço completo, como /Volumes/ArquivosDaEmpresa/Documentos. No Mac, "
            "as pastas compartilhadas conectadas normalmente ficam dentro de /Volumes.",
        ),
        Problem(
            "raiz não existe",
            "O painel não achou nenhuma pasta nesse endereço.",
            "Confira letras, acentos e espaços. Veja se a pasta compartilhada está conectada a "
            "este computador (ela aparece no Finder). Se estiver, copie o endereço de lá.",
        ),
        Problem(
            "raiz não pode conter link simbólico ou '..'",
            "O endereço passa por um atalho (chamado de link simbólico) ou usa '..' para voltar "
            "uma pasta. Por segurança, o painel só aceita o endereço verdadeiro.",
            "Digite o endereço real da pasta, sem atalhos e sem '..'. Se não souber qual é, "
            "peça ao responsável técnico.",
        ),
        Problem(
            "raiz deve ser um diretório",
            "O endereço aponta para um arquivo, não para uma pasta (diretório é o nome técnico "
            "de pasta).",
            "Digite o endereço da pasta, não o de um arquivo dentro dela.",
        ),
        Problem(
            "raiz deve estar dentro de um ponto de montagem",
            "A pasta não está dentro de uma pasta compartilhada (ou disco) conectada a este "
            "computador. O agente só trabalha em pastas assim, para nunca mexer em lugar errado.",
            "Conecte a pasta compartilhada pelo Finder e copie o endereço dela de lá (normalmente "
            "começa com /Volumes). Se mesmo assim for recusado, chame o responsável técnico.",
        ),
        Problem(
            "raiz inacessível ou é link simbólico",
            "O painel não conseguiu abrir a pasta. Ela pode ser um atalho ou o programa pode "
            "não ter permissão para entrar nela.",
            "Confira o endereço. Se estiver certo, peça ao responsável técnico para liberar o "
            "acesso da conta do agente a essa pasta.",
        ),
        Problem(
            "aplicação não consegue listar a raiz",
            "O programa não tem permissão nem para ver o que há dentro da pasta.",
            "Peça ao responsável técnico para liberar o acesso da conta do agente.",
        ),
        Problem(
            "raiz mudou durante a validação",
            "A pasta foi trocada no servidor enquanto o painel conferia o endereço. Por "
            "segurança, nada foi cadastrado e nada foi gravado nela.",
            "Confira no Finder se a pasta é a certa e tente de novo. Se repetir, chame o "
            "responsável técnico.",
        ),
        Problem(
            "raiz mudou durante a validação (link simbólico)",
            "Enquanto o painel conferia, a pasta virou um atalho (link simbólico). Por "
            "segurança, nada foi cadastrado e nada foi gravado nela.",
            "Chame o responsável técnico: alguém ou algum programa mexeu na pasta.",
        ),
        Problem(
            "aplicação não tem permissão de escrita na raiz",
            "O programa vê a pasta, mas não consegue gravar nela. Sem poder gravar, o agente "
            "não conseguiria guardar documentos ali. O teste de escrita falhou.",
            "Peça ao responsável técnico para dar permissão de escrita à conta do agente. "
            "Nada foi cadastrado; depois da permissão, tente de novo.",
        ),
        Problem(
            "raiz já cadastrada",
            "Esta pasta já está na lista. Maiúsculas e minúsculas não fazem diferença.",
            "Procure a pasta na lista. Se estiver inativa, clique em Ativar. Não precisa "
            "cadastrar de novo.",
        ),
        Problem(
            "raiz aninhada em outra raiz cadastrada",
            "Esta pasta está dentro de uma raiz que já existe, ou contém uma raiz que já "
            "existe. Raízes não podem se sobrepor. Raízes desativadas também contam.",
            "Se a pasta já está dentro de uma raiz cadastrada, não precisa de outra. Se a raiz "
            "que atrapalha foi cadastrada por engano, chame o responsável técnico: o painel "
            "não edita nem apaga raízes.",
        ),
        Problem(
            "pasta monitorada deve ser um caminho relativo simples",
            "O caminho da pasta monitorada deve ser só o trecho que vem depois da raiz: sem "
            "barra no início nem no fim, sem barra invertida (\\), sem '..', sem '.' e sem "
            "barras duplas.",
            "Digite, por exemplo, Entrada ou Entrada/Novos. Não repita o endereço da raiz.",
        ),
        Problem(
            "pasta monitorada não existe",
            "Não há pasta com esse nome dentro da raiz. O painel não cria a pasta; só "
            "cadastra uma que já existe.",
            "Crie a pasta no Finder, dentro da raiz, e cadastre de novo.",
        ),
        Problem(
            "pasta monitorada deve ser um diretório",
            "O nome aponta para um arquivo, não para uma pasta.",
            "Digite o nome de uma pasta.",
        ),
        Problem(
            "pasta monitorada não pode conter link simbólico",
            "O caminho passa por um atalho (link simbólico).",
            "Digite o caminho verdadeiro da pasta.",
        ),
        Problem(
            "pasta monitorada já cadastrada nesta raiz",
            "Essa pasta monitorada já está na lista desta raiz.",
            "Procure-a na lista. Se estiver inativa, clique em Ativar.",
        ),
        Problem(
            "descrição inválido",
            "A descrição passou de 500 letras.",
            "Encurte a descrição. Ela é só um lembrete.",
        ),
        Problem("raiz não encontrado", _NOT_FOUND, _OPEN_AGAIN),
        Problem("pasta monitorada não encontrado", _NOT_FOUND, _OPEN_AGAIN),
    ),
    "template": (
        Problem(
            _NAME_REQUIRED,
            "O campo Nome ficou vazio.",
            "Digite o nome do departamento, por exemplo Financeiro.",
        ),
        Problem(
            "nome inválido",
            "O nome passou de 200 letras.",
            "Use um nome mais curto.",
        ),
        *FOLDER_NAME_PROBLEMS,
        Problem(
            "já existe departamento com este nome",
            "Há outro departamento com o mesmo nome. Maiúsculas e minúsculas não diferenciam. "
            "Um departamento inativo também conta: desativar não libera o nome.",
            "Se é o mesmo departamento de antes, ative o antigo de novo em vez de criar outro. "
            "Se não, use outro nome. Na dúvida, chame o responsável técnico.",
        ),
        Problem(
            "pasta já em uso por outro departamento",
            "Outro departamento já usa esse nome de pasta. Maiúsculas e minúsculas não "
            "diferenciam. Um departamento inativo também conta.",
            "Escolha um nome de pasta diferente para este departamento. Se é o mesmo "
            "departamento de antes, ative o antigo de novo.",
        ),
        Problem("departamento não encontrado", _NOT_FOUND, _OPEN_AGAIN),
        _FOLDERS_NOT_RENAMED,
    ),
    "donos": (
        Problem(
            "nome fantasia é obrigatório",
            "O campo Nome fantasia ficou vazio. É o único campo obrigatório do dono.",
            "Digite o nome pelo qual o dono é conhecido.",
        ),
        Problem(
            "nome fantasia inválido",
            "O nome fantasia passou de 200 letras.",
            "Use um nome mais curto. A forma completa pode ir na razão social.",
        ),
        Problem(
            "razão social inválido",
            "A razão social passou de 200 letras.",
            "Encurte a razão social, por exemplo abreviando “Limitada” para “Ltda”.",
        ),
        Problem(
            "CNPJ inválido",
            "Os números não formam um CNPJ válido: faltam ou sobram dígitos, ou os dois "
            "últimos números (que servem de conferência) não batem.",
            "Confira o CNPJ no documento original. Com ou sem pontos, barra e traço, ele tem "
            "14 números. Se o dono não tem CNPJ, deixe o campo vazio.",
        ),
        Problem(
            "CNPJ alfanumérico não suportado nesta versão",
            "O CNPJ tem letras. O formato novo, com letras, ainda não é aceito por esta versão.",
            "Deixe o campo CNPJ vazio e use os nomes alternativos. Avise o responsável técnico.",
        ),
        Problem(
            "CNPJ já cadastrado em outro dono",
            "Outro dono já usa esse CNPJ. Cada CNPJ só pode ter um dono. Um dono desativado "
            "continua com o CNPJ dele.",
            "Procure o dono na lista, também entre os inativos. Provavelmente ele já está "
            "cadastrado: se estiver inativo, ative-o de novo em vez de criar outro.",
        ),
        Problem(
            "pasta já em uso por outro dono",
            "Outro dono já usa esse nome de pasta. Maiúsculas e minúsculas não diferenciam. "
            "Um dono desativado continua ocupando a pasta dele.",
            "Escolha outro nome de pasta para este dono. Se é o mesmo dono de antes, ative o "
            "antigo de novo.",
        ),
        Problem(
            "alias já pertence a outro dono",
            "Um dos nomes alternativos (na tela, 'alias') já pertence a outro dono. Cada nome "
            "só pode apontar para um dono, mesmo que ele esteja desativado. O nome fantasia e a "
            "razão social também contam como nomes alternativos.",
            "Tire esse nome da lista, ou apague-o do outro dono antes. Se os dois donos são "
            "mesmo a mesma empresa, use um só cadastro.",
        ),
        Problem(
            "alias já cadastrado",
            "Esse nome alternativo já existe, neste dono ou em outro. O painel guarda os "
            "nomes sem acento, sem pontuação e em letras minúsculas, então 'Alfa' e 'ALFA' "
            "são o mesmo nome.",
            "Veja a lista de nomes alternativos do dono. Se o nome está em outro dono, abra "
            "esse outro dono e decida qual deve ficar com ele.",
        ),
        Problem(
            "alias deve conter letras ou números",
            "O nome alternativo só tinha sinais, como '---' ou '...'.",
            "Digite um nome com pelo menos uma letra ou um número.",
        ),
        Problem(
            "alias não encontrado",
            "O nome alternativo já tinha sido removido, por exemplo em outra aba do navegador.",
            "Abra o dono de novo pela lista de donos e confira os nomes alternativos.",
        ),
        *FOLDER_NAME_PROBLEMS,
        Problem(
            "dono não encontrado",
            "O dono que você abriu não existe (o endereço da página pode estar errado ou antigo).",
            "Volte para a lista de donos e clique no nome do dono.",
        ),
        _FOLDERS_NOT_RENAMED,
    ),
    "usuarios": (
        Problem(
            "Telegram ID deve ser só números. Não é o telefone nem o @nome: peça o número "
            "à própria pessoa.",
            "O campo Telegram ID só aceita números, sem letras, espaços ou sinais. O navegador "
            "também pode barrar o envio antes, com um aviso dele.",
            "Digite só o número da pessoa no Telegram. Não é o telefone nem o @nome. Peça o "
            "número à própria pessoa ou ao responsável técnico.",
        ),
        Problem(
            "telegram_id deve ser um inteiro positivo",
            "O número do Telegram precisa ser maior que zero.",
            "Confira o número com a pessoa.",
        ),
        Problem(
            "telegram_id já cadastrado",
            "Já existe alguém com esse número. Isso vale também para pessoas desativadas e "
            "para o administrador.",
            "Procure a pessoa na lista. Se estiver inativa, clique em Ativar.",
        ),
        Problem(
            _NAME_REQUIRED,
            "O campo Nome ficou vazio.",
            "Digite o nome da pessoa.",
        ),
        Problem(
            "nome inválido",
            "O nome passou de 200 letras.",
            "Use um nome mais curto.",
        ),
        Problem("usuário não encontrado", _NOT_FOUND, _OPEN_AGAIN),
        Problem(
            "departamento deve ser um número inteiro",
            "Nenhum departamento foi escolhido. Isso acontece quando ainda não existe "
            "nenhum departamento ativo. Nesse caso a página mostra um aviso no lugar do "
            "formulário.",
            "Cadastre antes os departamentos, na seção Template. Depois volte e cadastre a pessoa.",
        ),
        Problem(
            "departamento inativo",
            "O departamento escolhido está desativado. Só departamentos ativos podem receber "
            "uma pessoa nova ou uma troca.",
            "Escolha outro departamento, ou ative este de novo na seção Template.",
        ),
        Problem(
            "não é possível desativar o último administrador ativo",
            "Esta é a única pessoa com papel de administrador. Sem ela, ninguém poderia "
            "mexer na configuração.",
            "Não desative. Para trocar de administrador, chame o responsável técnico.",
        ),
    ),
    "modelo": (
        Problem(
            "nome do modelo é obrigatório",
            "O campo Nome do modelo ficou vazio.",
            "Digite o nome do modelo, do jeito que o responsável técnico informou.",
        ),
        Problem(
            "nome de modelo inválido",
            "O nome tem espaço ou algum sinal que não é aceito, ou é longo demais. São aceitos "
            "letras, números e os sinais : . _ / - (até 128 caracteres, começando por letra "
            "ou número).",
            "Digite o nome exatamente como o responsável técnico passou, sem espaços.",
        ),
        Problem(
            "nome do modelo inválido",
            "Parecida com a frase acima, mas aqui o nome passou de 200 letras.",
            "Confira se não colou um texto a mais. O nome do modelo é curto.",
        ),
        Problem(
            "digest de modelo inválido",
            "O digest (o código que identifica a versão exata do modelo) tem espaço ou sinal "
            "que não é aceito. São aceitos letras, números e os sinais : . _ - (até 128 "
            "caracteres).",
            "Copie o código inteiro, sem espaços. Se não tiver o código, deixe o campo vazio.",
        ),
        Problem(
            "digest do modelo inválido",
            "Parecida com a frase acima, mas aqui o digest passou de 200 letras.",
            "Confira se não colou um texto a mais. Se não tiver o código, deixe o campo vazio.",
        ),
        Problem(
            "provedor externo exige OSA_EXTERNAL_LLM_API_KEY configurada no ambiente",
            "Você marcou Ligar provedor externo, mas a chave de acesso desse serviço não foi "
            "instalada no computador. Sem a chave, o painel não deixa ligar.",
            "Desmarque a opção. Para usar o provedor externo, peça ao responsável técnico "
            "para instalar a chave. Nunca digite a chave no painel.",
        ),
    ),
    "problemas": (
        Problem(
            STALE_PAGE_TEXT,
            "Erro 403 ao salvar: a página mostra só este texto. A página em que você estava "
            "ficou velha demais. Isso acontece depois que o painel é desligado e religado. "
            "Nada foi salvo.",
            "Não use o botão de recarregar nessa tela: ele repetiria o envio antigo. "
            + _OPEN_AGAIN
            + " Se o erro voltar, chame o responsável técnico.",
            anchor="erro-403",
        ),
        Problem(
            "forbidden",
            "Erro 403, só com esta palavra na página. O painel recusou o pedido porque ele "
            "veio de outro computador ou de um endereço que o painel não aceita.",
            "Abra o painel no computador dele, sempre pelo endereço http://127.0.0.1:8765. "
            "Se continuar, chame o responsável técnico.",
            anchor="erro-endereco",
        ),
        Problem(
            "conflito com o cadastro existente",
            "O que você tentou salvar bate com algo que já existe, por exemplo duas pessoas "
            "salvando o mesmo item ao mesmo tempo. Nada foi salvo.",
            _OPEN_AGAIN + " Veja o que já está cadastrado antes de salvar de novo.",
        ),
        Problem(
            "requisição inválida",
            "O painel não entendeu o pedido do navegador. Costuma ser uma página muito antiga "
            "ou um endereço digitado errado.",
            "Volte ao início do painel e refaça o caminho clicando nos menus.",
        ),
        Problem(
            "erro interno",
            "Algo falhou dentro do painel, por exemplo o banco de dados está desligado. "
            "Não é culpa sua. O painel não mostra detalhes, de propósito.",
            "Tente de novo em um minuto. Se continuar, chame o responsável técnico e diga o "
            "que estava fazendo.",
        ),
        Problem(
            BLOCKED_TITLE,
            "Aparece numa caixa no alto de todas as páginas, com o texto "
            f"'{BLOCKED_TEXT}'. Ainda não existe um administrador, então o painel só deixa "
            "olhar: os formulários ficam escondidos.",
            "Chame o responsável técnico para criar o primeiro administrador. Isso não pode "
            "ser feito pelo painel.",
            anchor="bloqueado",
        ),
    ),
}


def all_problems() -> tuple[Problem, ...]:
    """Every quoted message, in page order (used by the tests)."""
    return tuple(problem for group in PROBLEMS.values() for problem in group)
