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
Usuário que configura a instalação, confirma dono novo e é o único que acessa classe sensível.

**Operador**:
Usuário da allowlist que envia e pede documentos.

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
