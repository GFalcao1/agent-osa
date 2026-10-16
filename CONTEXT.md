# Agent O.S.A. — vocabulário

Arquivamento e consulta de documentos de uma empresa numa árvore padronizada de
pastas, por Telegram, com o modelo escrevendo o envelope e código fazendo a entrega.

## Linguagem

### Organização

**Instalação**:
Uma implantação do agente para uma empresa: um bot, um banco, um conjunto de raízes.
_Evitar_: tenant, cliente (ambíguo com dono)

**Dono**:
Pessoa jurídica ou física a quem um documento pertence; tem uma árvore própria dentro de cada raiz.
_Evitar_: coleção, empresa (é só um tipo de dono), cliente

**Alias**:
Nome alternativo cadastrado de um dono (fantasia, razão social, abreviação) que resolve para o mesmo dono.

**Evidência de dono**:
CNPJ ou alias do cadastro encontrado por busca exata no texto do documento, ou dono declarado pelo operador. Única base aceita para arquivamento automático.
_Evitar_: dono inferido pelo modelo

**Departamento**:
Nível fixo abaixo do dono na árvore, definido pelo template da instalação; também o setor de um usuário.
_Evitar_: categoria, setor (para a pasta), subpasta

**Template**:
Lista ordenada de departamentos da instalação, com placeholder de ano opcional, aplicada igual a todo dono.
_Evitar_: estrutura padrão, árvore

**Raiz**:
Pasta do share cadastrada como limite de toda operação; nenhuma leitura ou escrita acontece fora dela.
_Evitar_: local autorizado, root, pasta global

**Pasta monitorada**:
Pasta dentro de uma raiz onde arquivos aparecem sem dono declarado e o worker os processa quando estáveis.
_Evitar_: inbox, entrada

**Arquivo estável**:
Arquivo da pasta monitorada cujo tamanho e mtime não mudaram por dois ciclos e que atingiu a idade mínima.

**Painel**:
Página web local de configuração (FastAPI + HTML) em `127.0.0.1:8765`. Sem autenticação própria na V1: só é alcançável na máquina (ADR-0005). Cadastra raízes, pastas monitoradas, template, donos, usuários e modelo; nunca cria o primeiro administrador.
_Evitar_: dashboard, console, backoffice

**Manual de uso**:
Página de ajuda embutida no painel (`/manual`), em português simples, para quem opera o painel sem ser técnico: ordem de configuração, um capítulo por seção, glossário, problemas comuns, segurança e quando chamar o responsável técnico. Estática, sempre alcançável (também sem administrador e com cadastro vazio) e sem tocar o banco; cada página do painel tem um bloco "Como usar esta página" que leva ao capítulo certo. As frases de recusa que ela cita são conferidas por teste contra o código que as gera.
_Evitar_: README do operador, tutorial, wiki

**Bootstrap**:
`osa admin bootstrap`: cria o primeiro e único administrador, antes de qualquer cadastro. Recusa se já existir um administrador, ativo ou não.

**Cadastro**:
O conjunto configurado pelo administrador (raízes, pastas monitoradas, departamentos, donos com aliases, usuários, modelo). O banco é a fonte de verdade; YAML é só export/import.
_Evitar_: configuração (é o que vem do ambiente), seed

**Import**:
Carga de um YAML do cadastro num banco migrado com administrador já criado pelo bootstrap e cadastro vazio (sem raiz, departamento, dono, usuário não-admin nem configuração do modelo gravada). Não faz merge e nunca cria administrador.

**Sonda de escrita**:
Arquivo `.osa-probe-<uuid>` criado e removido pela aplicação ao cadastrar uma raiz, para provar que ela lista e escreve ali. Roda mesmo com efeitos de filesystem desligados; é a única exceção a "sem delete" e só toca o próprio arquivo. Roda só depois de recusadas raiz duplicada e aninhada; o evento `root.probe` fica gravado mesmo que o cadastro da raiz seja revertido.

**Configuração do modelo**:
Nome, digest e interruptor do provedor externo gravados no banco (linha única); gravar toma o mesmo lock do import, que nunca a sobrescreve. A chave do provedor externo nunca é gravada: vive só em `OSA_EXTERNAL_LLM_API_KEY`. O interruptor só vale com a chave presente.

### Carta e correio

**Envelope**:
JSON validado por schema que o modelo devolve: classificação de um documento ou interpretação de um pedido. Nunca contém dono.
_Evitar_: resposta do modelo, output

**Correio**:
Código determinístico que confere evidência de dono, template e raiz, e executa o arquivamento.
_Evitar_: executor, agente

**Arquivamento**:
Rename de um arquivo para a pasta de destino dentro da mesma raiz, com sufixo em colisão e sem sobrescrita.
_Evitar_: mover, organizar, upload

**Arquivamento automático**:
Arquivamento sem humano, permitido só com evidência de dono e envelope sem reserva.

**Revisão**:
Caso que o correio não arquivou sozinho, entregue ao revisor com botões para decidir dono, departamento ou deixar na origem.
_Evitar_: aprovação, plano

**Desfazer**:
Rename inverso de um arquivamento registrado, dentro da mesma raiz, nunca apagando.

### Pessoas

**Administrador**:
Usuário que configura a instalação, confirma dono novo e é o único que acessa classe sensível. Único papel que pode não ter departamento.

**Operador**:
Usuário da allowlist que envia e pede documentos. Sempre tem departamento.

**Revisor**:
Operador que recebe revisões do seu departamento.

**Allowlist**:
Lista de IDs do Telegram cadastrados; qualquer outro ID é ignorado.

### Documentos

**Classe sensível**:
Arquivo de certificado digital ou chave privada, identificado por extensão: nunca extraído, nunca enviado ao modelo nem pelo Telegram.
_Evitar_: certificado (ambíguo com certidão em PDF)

**Não suportado**:
Formato sem extração implementada; fica na origem e gera aviso.

**Índice**:
Registro em banco de cada documento conhecido: hash, caminho, dono, departamento, metadados do envelope, texto extraído e estado.
_Evitar_: banco de hashes, catálogo

**Duplicata**:
Arquivo cujo sha256 já consta no índice; é informado, não arquivado de novo.

**Scan**:
Leitura das raízes para alimentar o índice com o que já está na árvore, sem mover nada.

**Estado**:
Posição do documento no índice: `DETECTADO`, `ESTAVEL`, `EXTRAIDO`, `CLASSIFICADO`, `ARQUIVADO`, `EM_REVISAO`, `NA_ORIGEM`, `NAO_SUPORTADO`, `FALHOU`. As transições são regra do correio, não do banco.

**Origem**:
Como o documento entrou no índice: `telegram`, `watched` (pasta monitorada) ou `scan`.

**Auditoria**:
Registro append-only de eventos (ator, ação, alvo, hash, resultado); o banco rejeita UPDATE, DELETE e TRUNCATE. Nunca recebe texto de documento nem segredo. Atores do cadastro: `panel:local` (painel) e `cli:<comando>` (`cli:bootstrap`, `cli:import`). Recusas de bootstrap e de raiz (`resultado = denied`) e a sonda de escrita (`root.probe`) são gravadas numa transação própria e por isso sobrevivem ao rollback da operação recusada (inclusive de um import revertido); não é vazamento da transação.

## Tabelas

Nomes em inglês, colunas em português.

| Conceito | Tabela |
|---|---|
| Usuário (allowlist, papel) | `users` |
| Dono | `owners` |
| Alias | `owner_aliases` |
| Departamento (template) | `departments` |
| Raiz | `roots` |
| Pasta monitorada | `watched_folders` |
| Índice | `documents` |
| Arquivamento (operações, para desfazer) | `archive_operations` |
| Revisão | `review_cases` |
| Auditoria | `audit_events` |
| Configuração do modelo (linha única) | `installation_settings` |

Papéis: `admin`, `operator`, `reviewer`. Modos de arquivamento: `auto`, `review`, `undo`. Estados de revisão: `aberto`, `decidido`, `origem`.
