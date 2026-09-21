# Agent O.S.A.

**Organizator Sender Archver** — automação documental com IA, aprovação humana e rastreabilidade.

O O.S.A. é um projeto de organizador local de documentos e pastas para qualquer usuário, em uso pessoal ou compartilhado. Pode organizar documentos de empresas, arquivos pessoais, documentos de pacientes, materiais de estudo, projetos, imóveis e outros assuntos. A interação inicial será pelo Telegram, com interpretação de pedidos em linguagem natural e execução limitada por regras determinísticas.

O projeto também tem um objetivo educacional: construir uma aplicação que seja possível estudar, explicar, modificar manualmente e evoluir com apoio de ferramentas de AI Coding.

## Estado atual

O repositório contém o planejamento de **32 tasks**, a base de configuração e valores iniciais de domínio. A TASK-001 foi aceita; os demais gates continuam pendentes. Os fluxos de organização ainda não estão implementados.

As funcionalidades descritas são planejadas. O escopo geral do produto e o recorte inicial da V1 são diferenciados abaixo; não representam capacidades já disponíveis.

## O que o projeto pretende resolver

### Organizar uma coleção de documentos

Ao receber um pedido como “Organize os documentos do Projeto Aurora”, o sistema deverá identificar o usuário, verificar suas permissões, analisar documentos da pasta de entrada e propor destinos nas pastas da coleção.

O usuário revisará o plano antes de aprovar qualquer movimentação. Documentos ambíguos permanecerão na origem para revisão.

### Localizar e enviar documentos por e-mail

Um pedido como “Envie o contrato e o relatório mais recente do Projeto Aurora por e-mail” deverá localizar os arquivos, verificar pertencimento e versões, apresentar os anexos e o destinatário e solicitar confirmação antes do envio.

Documento não encontrado será informado como ausente. O modelo não deverá inventar arquivos ou presumir versões.

### Organizar um lote

O sistema deverá analisar um conjunto definido de documentos, identificar coleção, tipo documental, categoria e data/período quando aplicável, e apresentar um resumo com itens elegíveis e pendentes de revisão.

Somente os itens aprovados poderão ser movimentados, com registro do resultado de cada operação.

Uma **coleção documental** reúne arquivos de um contexto escolhido pelo usuário.
“Empresa fictícia Alfa”, “Arquivo pessoal”, “Paciente fictício Alfa” e “Projeto
Aurora” são exemplos. Empresas continuam incluídas; o produto não é exclusivo
de um setor. CNPJ e outros identificadores podem ser referências do contexto
correspondente, sem se tornarem requisitos para todos os usuários. Categorias e tipos
são configuráveis por coleção. A pessoa ou assunto representado pela coleção não
precisa ter conta no sistema; acesso depende das concessões do usuário.

### Tipos, formatos e estruturas de organização

O produto deve permitir diferentes tipos documentais, formatos de arquivo e
estruturas de pastas, conforme a necessidade de cada usuário. Por exemplo:

| Contexto | Documentos | Exemplo de organização |
|---|---|---|
| Empresa fictícia Alfa | Contratos, notas e relatórios | Por departamento, ano ou projeto |
| Paciente fictício Beta | Exames e relatórios | Por paciente, atendimento ou data |
| Arquivo pessoal | Recibos, comprovantes e fotografias | Por assunto, evento ou período |
| Projeto Aurora | Especificações, planilhas e imagens | Por projeto, etapa ou tipo |

Esses exemplos são combináveis e não impõem uma árvore única. Uma coleção é um
agrupamento lógico; ela não exige uma pasta com o mesmo nome. A organização pode
usar diretórios existentes e hierarquias configuradas em locais autorizados.

PDFs, documentos de texto, planilhas e imagens fazem parte da direção do produto.
Cada formato precisa de suporte explícito às operações aplicáveis antes de ser
habilitado. **O recorte inicial planejado da V1 processa PDFs textuais e
digitalizados**; os demais formatos ainda precisam de tasks de implementação e
validação e permanecem preservados na origem enquanto não forem suportados.

## Princípios da solução

- **Planejar, aprovar, executar:** toda organização exige um plano persistido e aprovação vinculada à versão revisada.
- **IA com capacidades limitadas:** o modelo interpreta e classifica; regras determinísticas controlam acesso, destinos e efeitos.
- **Segurança de arquivos:** operações restritas a diretórios configurados, sem exclusão de documentos ou sobrescrita na V1.
- **Dry-run por padrão:** desenvolvimento começa com simulações, dados fictícios e integrações de teste.
- **Evidências antes de ações:** confiança declarada pelo modelo não substitui validações de identidade e conteúdo.
- **Auditoria:** pedidos, decisões, aprovações, movimentações e envios devem ser rastreáveis.
- **Falhas explícitas:** resultados incertos exigem reconciliação; não autorizam repetição automática de efeitos.
- **Código explicável:** responsabilidades claras, testes e documentação acompanhando a implementação.

O planejamento prevê um painel web administrativo para cadastrar várias pastas
por coleção, em discos ou compartilhamentos independentes. Cada local terá ações,
alcance, limites e rotas de movimentação explícitos. O backend combina essas regras
com as permissões do solicitante a cada acesso. Leitura não concede envio; cadastro
não substitui aprovação de movimentações. Não há pasta global obrigatória.

Detalhes e transição: [política de acesso a arquivos](docs/FILE_ACCESS_POLICY.md).
O painel e essa política ainda serão implementados; a base atual não os oferece.

## Arquitetura proposta

Um monólito modular: um pacote Python e uma implantação, com processos locais separados para API, comunicação com Telegram e execução de trabalhos.

O fluxo previsto é:

```text
Telegram
  → API local: autenticação e recebimento do pedido
  → PostgreSQL: solicitação persistida
  → Worker + LangGraph: coordenação do workflow
  → Serviços: regras e validações determinísticas
  → Filesystem restrito / provider de e-mail
  → Auditoria e resposta ao usuário
```

O LLM participa da interpretação de pedidos e da classificação documental com saída estruturada. Ele não terá acesso livre ao computador.

Os documentos continuarão no filesystem. PostgreSQL armazenará catálogo, metadados, permissões, planos, estados e auditoria. A integração de e-mail terá um contrato próprio para permitir a troca de provider.

## Stack prevista

| Área | Tecnologia |
|---|---|
| Linguagem | Python 3.12+ |
| API | FastAPI |
| Workflows | LangGraph |
| Contratos e configuração | Pydantic e pydantic-settings |
| Persistência | PostgreSQL, SQLAlchemy e Alembic |
| Telegram | python-telegram-bot |
| Leitura documental | PyMuPDF, com OCR local somente como fallback |
| Filesystem | Abstrações próprias sobre pathlib, shutil e primitivas restritas quando necessárias |
| Testes e qualidade | pytest, Ruff e mypy |

A V1 proposta começa com PDFs textuais e digitalizados. Outros formatos permanecem fora do processamento automático até receberem suporte explícito.

## Como a estrutura será criada

**A estrutura de código será criada progressivamente, conforme as tasks.** Cada task informa os arquivos que deve criar, modificar e preservar, além de testes, critérios de aceitação e rollback.

Não serão criados módulos vazios apenas para reproduzir uma árvore arquitetural. As pastas aparecerão quando houver uma responsabilidade concreta a implementar.

Estrutura existente nesta etapa:

```text
agent-osa/
├── README.md
├── tasks/
│   ├── README.md
│   ├── GATES.md
│   └── TASK-001.md … TASK-032.md
└── docs/
    └── glossary/
        ├── README.md
        └── referências por módulo
```

Os caminhos de código descritos nos glossários são previstos. Sua existência e seus contratos serão confirmados durante a implementação.

## Desenvolvimento por etapas

A progressão funcional será:

1. Fundação, identidade e leitura segura.
2. Busca somente leitura pelo Telegram.
3. Envio de documentos por e-mail.
4. Classificação individual e por lote.
5. Planos de organização e aprovação humana.
6. Movimentação controlada e recuperação de falhas.
7. Testes completos e homologação operacional.

O trabalho acontece **uma task por vez**. Uma etapa só termina depois das verificações exigidas e da revisão prevista no gate. Falhas bloqueiam o avanço.

## Por onde começar

- [Índice das tasks e dependências](tasks/README.md)
- [TASK-001 — Fundação executável e configuração segura](tasks/TASK-001.md)
- [Gates de qualidade e de fase](tasks/GATES.md)
- [Glossário da arquitetura e roteiro de aprendizado](docs/glossary/README.md)

A base de configuração segura está disponível localmente. Para instalar as
dependências reproduzíveis e executar a suite portátil inicial:

```sh
uv sync --group dev
uv run pytest tests/unit/test_settings.py -q
```

Ela inicia com simulação ativa, movimentação de arquivos e LLM externo
desabilitados, e provider de e-mail de desenvolvimento. A aplicação ainda não
possui processos executáveis; eles serão adicionados nas tasks que materializam
API, worker e integração Telegram.
