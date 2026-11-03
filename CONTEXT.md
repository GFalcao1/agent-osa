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
CNPJ ou alias do cadastro encontrado por busca exata no texto do documento, ou dono declarado pelo operador. Única base aceita para arquivamento automático. O dono declarado vence a inferência; um texto que identifica só outro dono cadastrado leva o caso à revisão (`dono_divergente`), e vários donos sem declaração é ambíguo.
_Evitar_: dono inferido pelo modelo

**Dono declarado**:
Dono que o operador nomeou por alias ou CNPJ exato e que o código resolveu no cadastro (`osa.courier.owners`), nunca o modelo.

**Pedido de dono**:
Dono novo citado por operador e desconhecido do cadastro; fica pendente até um administrador ativo confirmar (cria o dono e a árvore do template na raiz do upload) ou recusar. Só a confirmação cria algo; as duas respostas são auditadas.

**Raiz do upload**:
Raiz cuja quarentena recebe o upload de um dono: a primeira raiz ativa, na ordem do cadastro, que já tem a pasta do dono; senão, a primeira raiz ativa.

**Departamento**:
Nível fixo abaixo do dono na árvore, definido pelo template da instalação; também o setor de um usuário.
_Evitar_: categoria, setor (para a pasta), subpasta

**Descrição do departamento**:
Frase curta e opcional (até 160 caracteres) que diz o que um departamento arquiva, cadastrada pelo administrador. Vai ao modelo num bloco de dados próprio do prompt, delimitado com o nonce, fora do delimitador do documento; descrição que soe como instrução ao modelo é recusada antes da chamada e pelo cadastro (painel e import, mesma regra); nunca entra no enum nem no schema.
_Evitar_: categoria, rótulo, dica

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

**Observação**:
O que o worker sabe de um arquivo da pasta monitorada (`watch_entries`): caminho, tamanho, mtime, ciclos sem mudança e estado (`DETECTADO`, `ESTAVEL`, `PROCESSADO`). Existe porque um arquivo em cópia ainda não pode ser lido, então não tem hash nem linha no índice. Sai quando o arquivo sai da pasta (salvo com aviso ainda por entregar); `PROCESSADO` é pulado enquanto tamanho e mtime não mudam. Guarda também as tentativas do correio (até três) e o aviso aos revisores até ser entregue (caixa de saída).
_Evitar_: documento (é a linha do índice), fila

**Worker**:
O segundo processo (`osa-worker`, `osa worker`): ciclos da pasta monitorada, rescan periódico em partes e lembrete diário. Avisa os revisores pelo mesmo bot; nunca recebe botões.
_Evitar_: daemon, agente

**Painel**:
Página web local de configuração (FastAPI + HTML) em `127.0.0.1:8765`. Sem autenticação própria na V1: só é alcançável na máquina (ADR-0005). Cadastra raízes, pastas monitoradas, template, donos, usuários e modelo; nunca cria o primeiro administrador.
_Evitar_: dashboard, console, backoffice

**Confirmação**:
Passo que o painel coloca antes de desativar um item ou remover um alias: o primeiro envio devolve a mesma página com uma faixa que nomeia o item e dois botões (confirmar e cancelar), e só o envio com `confirmar=1` altera o cadastro. Ativar não pede confirmação, e uma desativação que o cadastro sempre recusaria (o último administrador ativo) é recusada direto, sem a pergunta.
_Evitar_: diálogo, aviso

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

**Delimitador**:
Par de marcas `<<<DOCUMENTO {nonce}>>>` e `<<<FIM DOCUMENTO {nonce}>>>` entre as quais o texto de um documento (ou de um pedido) vai ao modelo como dado. O nonce é sorteado a cada chamada e o hash do prompt cobre o template, não a instância. Reduz a chance de o documento fechar o bloco; não obriga o modelo a obedecer.
_Evitar_: separador, escape

**Digest observado**:
Digest do modelo instalado, lido do servidor (`/api/tags`) e comparado ao digest configurado; com digest configurado, diferença ou ausência impede a chamada. Todo relatório de avaliação registra o configurado e o observado.
_Evitar_: versão do modelo

**Corpus de avaliação**:
Pasta com `corpus.json` e os documentos que o `osa eval` roda por um ou mais modelos: positivos (departamento esperado) e adversariais (ataques). O fictício fica em `tests/fixtures/corpus`; o do piloto fica fora de qualquer repositório e nunca é lido de dentro de um.
_Evitar_: dataset, benchmark, golden set

**Efeito indevido**:
Documento adversarial cujo envelope é válido e traz `precisa_revisao = false`: o correio o arquivaria sozinho se houvesse evidência de dono. Envelope inválido não conta, porque vira revisão. Critério de gate: zero. Adversarial que não chegou ao modelo (falha de extração) não conta como efeito, mas impede a meta.
_Evitar_: falha, vazamento

**Correio**:
Código determinístico que confere evidência de dono, template e raiz, e executa o arquivamento.
_Evitar_: executor, agente

**Arquivamento**:
Rename de um arquivo para a pasta de destino dentro da mesma raiz, com sufixo em colisão e sem sobrescrita. Sem rename exclusivo comprovado no volume, ou com nome que não cabe no share, é recusado sem efeito e o caso vai para revisão.
_Evitar_: mover, organizar, upload

**Arquivamento automático**:
Arquivamento sem humano, permitido só com evidência de dono e envelope sem reserva.

**Quarentena**:
Pasta `.osa-quarentena` dentro de cada raiz onde o upload é gravado, com nome exclusivo (um id mais a extensão normalizada do original), até o arquivamento. Fica no mesmo volume da árvore, então arquivar é rename. Criada pela aplicação na primeira gravação de upload, só com efeitos de filesystem ligados; nunca é destino de arquivamento nem entra no scan. O arquivo de pasta monitorada não passa por ela.
_Evitar_: inbox, área temporária, pasta de entrada

**Revisão**:
Caso que o correio não arquivou sozinho, entregue ao revisor com botões para decidir dono, departamento ou deixar na origem. A decisão passa pelo correio (`osa.courier.review`): arquivar grava a operação com `modo = review` e fecha o caso como `decidido`; deixar na origem fecha como `origem` com o documento `NA_ORIGEM`.
_Evitar_: aprovação, plano

**Rascunho de revisão**:
Dono e departamento já escolhidos nos botões de um caso, guardados na memória do bot até "Arquivar"; some com o reinício do bot e o revisor escolhe de novo. Não é registro.
_Evitar_: decisão parcial

**Pasta de espera**:
Pasta local, fora de qualquer raiz (`OSA_QUARANTINE_DIR` ou o temporário do sistema), onde o bot grava um download quando os efeitos de arquivo estão desligados, para o correio simular; a cópia é removida depois. Nunca é raiz, quarentena nem destino.
_Evitar_: quarentena local

**Simulado**:
Resultado previsto de um efeito de filesystem que não aconteceu porque os efeitos estão desligados (`simulated=True`). Calculado com o estado observado no momento; não promete ser igual ao da execução real.
_Evitar_: dry-run, teste

**Desfazer**:
Rename inverso de um arquivamento registrado, dentro da mesma raiz, conferido pelo hash gravado na operação, nunca apagando. O documento volta a `NA_ORIGEM` e a operação inversa fica registrada (`modo = undo`); desfazer duas vezes é recusado.

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
Registro em banco de cada documento conhecido: hash, caminho, dono, departamento, metadados do envelope, texto extraído (com `texto_tsv` gerado pelo banco para a busca) e estado; mais `visto_em` e `ausente_em`, o que o scan sabe do arquivo no disco.
_Evitar_: banco de hashes, catálogo

**Envelope de intenção**:
O que o modelo devolve para uma mensagem de texto do operador: `acao` (`buscar`, `listar_donos`, `listar_departamentos`, `status`, `ajuda`) mais os campos do envelope de consulta, planos no schema; no código, `consulta` é o envelope de consulta aninhado. A ação é despachada por código (`osa.search.intent`); o modelo nunca recebe resultado, nome de arquivo nem trecho de documento.
_Evitar_: comando, intenção do modelo

**Oferta**:
Os documentos que uma busca mostrou em botões a um usuário numa conversa, com os filtros entendidos; vive na memória do bot até o envio, o cancelamento ou o reinício. Um botão só envia documento da oferta de quem o aperta (dado de botão pode ser forjado).
_Evitar_: carrinho, seleção

**Caminho de rede**:
Endereço UNC de uma raiz (`\\servidor\compartilhamento`), cadastrado no painel (`roots.unc`, opcional); com o caminho relativo do documento, é o que o bot mostra quando o arquivo não vai pelo Telegram (acima do limite de envio, ou classe sensível para o administrador). Sem UNC cadastrado, o bot mostra o caminho nesta máquina.
_Evitar_: link, atalho

**Busca**:
Consulta ao índice (`osa.search.service.search`): FTS em português sobre nome, tipo, resumo e texto, mais filtros de dono, departamento, tipo e período resolvidos no cadastro. Dono ou departamento que o cadastro não conhece é resposta vazia com o filtro não entendido. Devolve até 5 `Hit` (id, caminho, nome, dono, departamento, ano, tipo, resumo, estado, se é sensível), nunca o texto, nunca ausente nem sem caminho.
_Evitar_: pesquisa semântica (V2), consulta (é o pedido do operador)

**Duplicata**:
Arquivo cujo sha256 já consta no índice como arquivado ou em revisão; é informado, não arquivado de novo. Um documento que falhou, ficou na origem ou não foi suportado é reprocessado na mesma entrada.

**Scan**:
Leitura das raízes para alimentar o índice com o que já está na árvore, sem mover nada (`osa scan`, `osa.worker.scan`). Dono e departamento vêm do caminho; o modelo só gera metadados. No padrão fica `ARQUIVADO`; fora dele, `NA_ORIGEM` com dono nulo. Retomável por raiz (`scan_cursor`, `scan_iniciado_em`, `ultimo_scan` em `roots`); o que começa com ponto fica de fora.
_Evitar_: indexação (é o resultado), varredura automática (é a TASK-111)

**Movido manualmente**:
Arquivo cujo hash o índice já conhece em outro caminho que não existe mais (caminhos comparados como o share: sem caixa nem forma Unicode): o scan atualiza caminho, dono e departamento na mesma entrada e audita `document.move` com `movido_manualmente`; só um estado que o caminho decidiu (`ARQUIVADO`, `NA_ORIGEM` fora do padrão) é recalculado. Se o caminho antigo ainda existe, a cópia nova é duplicata. Um documento que o scan deixou `NA_ORIGEM`, `NAO_SUPORTADO` ou `FALHOU` e depois subiu pelo Telegram é reaproveitado pelo correio na mesma entrada (o caminho passa à quarentena e ao destino); o arquivo original na árvore vira duplicata a cada scan.

**Ausente**:
Documento que um scan completo da sua raiz não encontrou no caminho registrado: `ausente_em` marcado, entrada mantida, fora da busca; o arquivo que volta limpa a marca. Um scan com `--desde`, retomado de outro com `--desde` diferente (ele recomeça), ou que encontrou pasta ou arquivo ilegível nunca marca ausente.
_Evitar_: apagado, removido

**Estado**:
Posição do documento no índice: `DETECTADO`, `ESTAVEL`, `EXTRAIDO`, `CLASSIFICADO`, `ARQUIVADO`, `EM_REVISAO`, `NA_ORIGEM`, `NAO_SUPORTADO`, `FALHOU`. As transições são regra do correio, não do banco.

**Origem**:
Como o documento entrou no índice: `telegram`, `watched` (pasta monitorada) ou `scan`.

**Auditoria**:
Registro append-only de eventos (ator, ação, alvo, hash, resultado); o banco rejeita UPDATE, DELETE e TRUNCATE. Nunca recebe texto de documento nem segredo. A chamada ao provedor externo grava `tentativa` antes da rede (guardada como `failed` com `fase = tentativa`, porque a tabela não tem "pendente") e `ok` ou `failed` só depois dela. Atores do cadastro: `panel:local` (painel) e `cli:<comando>` (`cli:bootstrap`, `cli:import`, `cli:process`, `cli:undo`, `cli:scan`), e o worker grava como `worker`; do bot: `telegram:<id do Telegram>`, inclusive para um id fora da allowlist (`telegram.unknown`, `denied`) e para o upload recusado antes do download (`telegram.upload`, `denied`). A decisão de revisão grava `review.decide` (ok, denied pelo share ou failed) ou `review.origin`, com `decidido_por`. O correio grava um evento por etapa do documento (`document.detect`, `document.extract`, `document.classify`, `document.evidence` quando o texto contradiz ou confunde o dono, `archive.intent`, `document.archive`, `document.review`, `document.fail`, `document.unsupported`, `document.duplicate`, `archive.undo`) e pelo pedido de dono (`owner.request.confirm`, `owner.request.refuse`, `owner.tree`); o scan grava na raiz `scan.start`, `scan.skip` (pasta ilegível), `scan.stop` e `scan.finish` (contagens) e no documento os mesmos eventos de etapa mais `document.index` (estado final, colocação e o departamento que o modelo disse), `document.sensitive` (classe sensível indexada só com hash e caminho), `document.move` e `document.absent`; a consulta grava `query.search` (filtros entendidos em ids e quantidade de resultados) e `document.send` (consulta, caminho, entrega `arquivo`/`caminho`/`recusado`, resultado), o cadastro do caminho de rede grava `root.unc`, e o worker grava `watch.detect` e `watch.skip` na observação, `scan.pause` ao parar o rescan na cota do ciclo e `reminder.daily` ao mandar (ou tentar) o lembrete, sempre com o sha256 e nunca com texto: um motivo de revisão escrito pelo modelo entra como `motivo_livre`; a recusa de classe sensível (`document.intake`, `denied`) não tem documento nem hash. Recusas de bootstrap e de raiz (`resultado = denied`) e a sonda de escrita (`root.probe`) são gravadas numa transação própria e por isso sobrevivem ao rollback da operação recusada (inclusive de um import revertido); não é vazamento da transação.

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
| Arquivamento (operações: raiz, caminhos relativos e hash, para desfazer) | `archive_operations` |
| Revisão | `review_cases` |
| Observação da pasta monitorada (trabalho do worker) | `watch_entries` |
| Auditoria | `audit_events` |
| Configuração do modelo (linha única) | `installation_settings` |

Papéis: `admin`, `operator`, `reviewer`. Modos de arquivamento: `auto`, `review`, `undo`. Estados de revisão: `aberto`, `decidido`, `origem`.
